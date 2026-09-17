"""第 94a 刀：图片资产活化 + 商品素材聚合集成测试（真 PG；ADR 0051）。

契约（施工单 Must 1/2/5/6）：
- 上传图片（png/jpeg/webp）登记出 kind=image 资产，对象键后缀**跟字节走**
  （报 png 传 jpeg 也写 .jpg 键）；类型闸 415 / 上限 413 / 空文件 422 / 未登录 401；
- **无 VLM key = 无草稿**（extracted 图片描述 = abstained）且照常待人洗——
  登记不因草稿缺位失败（fail-诚实）；有 key（替身）出草稿写 extracted
  （source=machine），**草稿不进索引**；
- 人洗 PATCH 图片描述（confirmed）→ 发布 → 切块=描述文本（不是二进制）、
  顾客问句命中该资产带引用、版本正文端点回落描述；
- 无描述的图片资产取正文 = 409（不静默空串）；
- ``GET /api/products/{id}/assets`` 聚合形状（kind/标题/状态/来源/线上版本号）、
  404/401、废弃资产不出现；
- 图片换字节（PUT …/bytes）按种类收图片、重跑草稿，文本类型 415。

云 VLM 用替身（monkeypatch ``services.vlm.describe_image``）——不打真网；真 VLM
验收需 key（仓库不带，closeout 记降级口径）。
"""

import base64
import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

import suite_api.services.vlm as vlm_service
from suite_api.services.vlm import VLMUnavailable

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

# 1x1 真 PNG / 最小 JPEG 头（魔数复验用；替身 VLM 不看像素）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
_JPEG = b"\xff\xd8\xff\xe0" + b"jfif-payload"

_DESCRIPTION = "显示器侧面带可调节支架，正面三边窄边框"
_DRAFT = "VLM 草稿：一个黑色的显示器，可能有支架"


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _fetch(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(os.environ[_URL_ENV]) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _product_id() -> int:
    rows = _fetch("SELECT id FROM products ORDER BY id LIMIT 1")
    assert rows
    return int(rows[0][0])


def _upload(
    client: TestClient,
    *,
    data: bytes = _PNG,
    name: str = "frame.png",
    content_type: str = "image/png",
    product_id: int | None = None,
    title: str | None = None,
) -> object:
    files = {"file": (name, data, content_type)}
    form: dict[str, str] = {}
    if product_id is not None:
        form["productId"] = str(product_id)
    if title is not None:
        form["title"] = title
    return client.post("/api/assets/register", files=files, data=form)


def _chunks(asset_id: int) -> list[str]:
    return [
        row[0]
        for row in _fetch(
            "SELECT chunk FROM retrieval_chunks WHERE asset_id = %s ORDER BY seq", (asset_id,)
        )
    ]


def _ask(client: TestClient, question: str) -> dict:
    """建会话问一句，返回 complete 事件载荷（引用由服务端定，与顾客通道同源）。"""
    created = client.post("/api/service/sessions")
    assert created.status_code == 201, created.text
    session_id = int(created.json()["id"])
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    complete = [payload for event, payload in events if event == "complete"]
    assert complete, f"没有 complete 事件: {events}"
    return complete[0]


# ---------- 1. 上传闸门与对象键 ----------


def test_register_image_requires_login_and_validates_upload(api: ApiFixture) -> None:
    client, _ = api
    assert _upload(client).status_code == 401
    _login(client)
    # 类型闸：非白名单 415；报了图片但字节不是图片同样 415（魔数复验）
    assert (
        _upload(client, data=b"hello", content_type="application/octet-stream").status_code == 415
    )
    lying = _upload(client, data="产品说明：净含量 550ml".encode(), content_type="image/png")
    assert lying.status_code == 415
    assert "魔数" in lying.json()["detail"]
    # 大小闸按种类分：图片 10MB（文本仍 2MB）
    assert _upload(client, data=_PNG + b"x" * (10 * 1024 * 1024)).status_code == 413
    # 空文件 422（先于魔数闸判——空字节不是「类型不符」）
    assert _upload(client, data=b"").status_code == 422


def test_registered_image_is_image_kind_with_byte_following_key(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    product_id = _product_id()
    resp = _upload(client, data=_PNG, name="frame.png", product_id=product_id, title="显示器商品图")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "image"
    assert body["status"] == "pending_review"  # 无 VLM key 也照常推进待人洗
    assert body["last_error"] is None
    assert body["versions"][0]["object_key"].endswith(".png")  # 键后缀跟字节走
    assert "#" not in body["versions"][0]["object_key"]  # 键不含产物语义（ADR 0003）

    # 报 png 传 jpeg：键跟**字节**走，不跟谎报的类型走
    lying = _upload(client, data=_JPEG, name="a.png", content_type="image/png")
    assert lying.status_code == 201
    assert lying.json()["versions"][0]["object_key"].endswith(".jpg")


# ---------- 2. 无 key = 无草稿（fail-诚实） ----------


def test_image_without_vlm_key_has_no_draft_and_text_endpoint_409(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    resp = _upload(client, title="无 key 商品图")
    assert resp.status_code == 201
    asset_id = int(resp.json()["id"])
    entry = resp.json()["versions"][0]["extracted_fields"]["图片描述"]
    assert entry == {"abstained": True}  # 无 key：无草稿，不写假值（0009）

    # 无描述的图片资产取正文 = 409（不静默空串：真相是「描述还没人写」）
    text = client.get(f"/api/assets/{asset_id}/versions/1/text")
    assert text.status_code == 409
    assert "图片描述" in text.json()["detail"]  # 文案语义化（第 94a 评审修）
    assert _chunks(asset_id) == []  # 未发布：索引里什么都没有


# ---------- 3. VLM 草稿：写 extracted、不进索引 ----------


def _enable_vlm(monkeypatch: pytest.MonkeyPatch, draft: str = _DRAFT) -> list[bytes]:
    calls: list[bytes] = []

    def fake_describe(image_bytes: bytes) -> str:
        calls.append(image_bytes)
        return draft

    monkeypatch.setattr(vlm_service, "describe_image", fake_describe)
    return calls


def test_vlm_draft_lands_in_extracted_but_never_enters_index(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    calls = _enable_vlm(monkeypatch)
    resp = _upload(client, title="有 VLM 的商品图")
    assert resp.status_code == 201, resp.text
    asset_id = int(resp.json()["id"])
    assert calls and calls[0] == _PNG  # 送进 VLM 的就是原字节
    entry = resp.json()["versions"][0]["extracted_fields"]["图片描述"]
    assert entry == {"value": _DRAFT, "source": "machine"}  # 草稿=机洗面，不是事实
    assert resp.json()["versions"][0]["confirmed_fields"] == {}

    # 不确认直接发布：草稿**不进索引**（VLM 只出草稿、人确认生效）
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200, published.text
    assert _chunks(asset_id) == []


def test_vlm_failure_is_no_draft_and_registration_still_succeeds(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)

    def _boom(_image_bytes: bytes) -> str:
        raise VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_service, "describe_image", _boom)
    resp = _upload(client, title="VLM 失败的商品图")
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending_review"  # 不 fail 整个登记（人洗兜底）
    assert resp.json()["last_error"] is None
    assert resp.json()["versions"][0]["extracted_fields"]["图片描述"] == {"abstained": True}


# ---------- 4. 人洗确认 → 发布 → 问句命中引用（e2e） ----------


def test_confirmed_description_is_indexed_and_question_hits_with_citation(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _enable_vlm(monkeypatch, draft=_DRAFT)  # 先有草稿，人洗在草稿上改写/确认
    product_id = _product_id()
    resp = _upload(client, product_id=product_id, title="显示器商品图 · 支架")
    assert resp.status_code == 201, resp.text
    asset_id = int(resp.json()["id"])

    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text
    confirmed = patched.json()["confirmed_fields"]["图片描述"]
    assert confirmed == {"value": _DESCRIPTION, "source": "human"}

    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200, published.text
    # 索引切块 = 描述文本（不是二进制、也不是草稿）
    assert _chunks(asset_id) == [_DESCRIPTION]
    # 版本正文端点回落描述（confirmed 优先）——「字节不动，正文出口收口」
    text = client.get(f"/api/assets/{asset_id}/versions/1/text")
    assert text.status_code == 200
    assert text.text == _DESCRIPTION

    # 顾客问图片内容词：命中该资产带引用（服务端定引用，模型无决定权）
    complete = _ask(client, "有带支架的显示器吗")
    cited = [c["asset_id"] for c in complete["citations"]]
    assert asset_id in cited, f"问句未命中该图片资产: {complete}"


def test_unconfirmed_image_is_not_retrievable(api: ApiFixture) -> None:
    """没描述（人没写、也没草稿）的图片发布后被问到时**不出现**（无正文块）——
    诚实：检索面只有人确认过的描述。"""
    client, _ = api
    _login(client)
    resp = _upload(client, title="无描述商品图 · 不可检索")
    asset_id = int(resp.json()["id"])
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    complete = _ask(client, "有没有带轮子的键盘")
    assert asset_id not in [c["asset_id"] for c in complete["citations"]]


# ---------- 5. 商品素材聚合端点 ----------


def test_product_assets_aggregates_kinds_and_requires_login(api: ApiFixture) -> None:
    client, _ = api
    product_id = _product_id()
    # 模块级共享 client：前面用例已登录——清 cookie 才能验 401（先例同其他集成套件）
    client.cookies.clear()
    assert client.get(f"/api/products/{product_id}/assets").status_code == 401
    _login(client)
    assert client.get("/api/products/999999/assets").status_code == 404

    image = _upload(client, product_id=product_id, title="聚合用商品图").json()
    doc = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", "净含量：550毫升".encode(), "text/plain")},
        data={"productId": str(product_id), "title": "聚合用文档"},
    ).json()

    rows = client.get(f"/api/products/{product_id}/assets").json()
    by_id = {row["id"]: row for row in rows}
    assert image["id"] in by_id and doc["id"] in by_id
    image_row = by_id[image["id"]]
    assert image_row["kind"] == "image"
    assert image_row["title"] == "聚合用商品图"
    assert image_row["status"] == "pending_review"
    assert image_row["source_kind"] == "upload"
    assert image_row["current_published_version_no"] is None  # 未发布=非权威
    assert image_row["product"]["id"] == product_id


def test_product_assets_excludes_discarded_and_published_flag(api: ApiFixture) -> None:
    """废弃资产不出现（与资产列表同口径）；已发布行带线上版本号（素材段
    「只有已发布是权威」的口径来源）。"""
    client, _ = api
    _login(client)
    product_id = _product_id()
    published = _upload(client, product_id=product_id, title="聚合用已发布图").json()
    assert client.post(f"/api/assets/{published['id']}/publish").status_code == 200

    rows = client.get(f"/api/products/{product_id}/assets").json()
    row = next(r for r in rows if r["id"] == published["id"])
    assert row["current_published_version_no"] == 1
    assert row["status"] == "published"

    # 废弃的失败资产：置 discarded 后从聚合消失（同 /api/assets 口径）
    failed = client.post(
        "/api/assets/register",
        files={"file": ("bad.txt", b"\xff\xfe\x00g\x00b", "text/plain")},
        data={"productId": str(product_id)},
    ).json()
    assert client.post(f"/api/assets/{failed['id']}/discard").status_code == 200
    rows_after = client.get(f"/api/products/{product_id}/assets").json()
    assert failed["id"] not in {r["id"] for r in rows_after}


# ---------- 6. 图片换字节（0042 出口在图片上可用） ----------


def test_replace_image_bytes_reruns_draft_and_rejects_text(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    resp = _upload(client, title="待换图的商品图")
    asset_id = int(resp.json()["id"])
    old_key = resp.json()["versions"][0]["object_key"]

    text_upload = client.put(
        f"/api/assets/{asset_id}/versions/1/bytes",
        files={"file": ("spec.txt", "产品说明。".encode(), "text/plain")},
    )
    assert text_upload.status_code == 415  # 种类不匹配：图片资产只收图片

    replaced = client.put(
        f"/api/assets/{asset_id}/versions/1/bytes",
        files={"file": ("new.jpg", _JPEG, "image/jpeg")},
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    new_key = body["versions"][0]["object_key"]
    assert new_key != old_key
    assert new_key.endswith(".jpg")  # 新键后缀跟新字节走（不按 kind 兜底成 .png）
    assert body["status"] == "pending_review"
    assert body["versions"][0]["extracted_fields"]["图片描述"] == {"abstained": True}  # 重跑草稿
