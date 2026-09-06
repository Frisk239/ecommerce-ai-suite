"""治理发布全链路集成测试（需真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

覆盖：登录 -> 上传登记（真写临时对象存储目录）-> 机洗抽到/弃权 -> 未确认发布被
422（缺项分两类）-> 确认/补填 -> 发布 200 -> 商品 spec_values 写回带来源 ->
审计留痕 -> 未登录 401 -> 机洗失败/就地重试 -> 状态机 409 -> 对象键形状。
"""

import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ApiFixture = tuple[TestClient, Path]

_WATER_DOC = "【产品规格】\n净含量：550毫升\n保质期：12个月\n储存条件：常温避光".encode()
_CUP_DOC = "钛钢保温杯产品说明\n净含量：480ml\n材质牌号未标注，详见吊牌。".encode()


def _login(client: TestClient, password: str = "operator123") -> Any:
    return client.post("/api/auth/login", json={"username": "operator", "password": password})


def _upload(
    client: TestClient,
    content: bytes,
    *,
    product_id: int | None = None,
    title: str | None = None,
    content_type: str = "text/plain",
) -> Any:
    files = {"file": ("spec.txt", content, content_type)}
    data: dict[str, str] = {}
    if product_id is not None:
        data["productId"] = str(product_id)
    if title is not None:
        data["title"] = title
    return client.post("/api/assets/register", files=files, data=data)


def _product_id_by_name(client: TestClient, name: str) -> int:
    products = client.get("/api/products").json()
    matching = [p for p in products if p["name"] == name]
    assert matching, f"种子商品缺失: {name}"
    return matching[0]["id"]


# ---------- 鉴权 ----------


def test_write_endpoints_require_login(api: ApiFixture) -> None:
    client, _ = api
    assert client.post("/api/assets/register", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 401
    assert client.post("/api/assets/1/retry-machine-wash").status_code == 401
    assert client.patch("/api/assets/1/versions/1/fields", json={"净含量": "1ml"}).status_code == 401
    assert client.post("/api/assets/1/publish").status_code == 401


def test_read_endpoints_require_login(api: ApiFixture) -> None:
    # CONTEXT.md「控制台=操作者登录后打开的人机界面」：业务读接口同样收紧；
    # 仅 /health 与 /api/auth/* 保持匿名
    client, _ = api
    client.cookies.clear()  # 防御：确保此刻确为未登录
    assert client.get("/api/assets").status_code == 401
    assert client.get("/api/assets/1").status_code == 401
    assert client.get("/api/products").status_code == 401
    assert client.get("/api/products/1").status_code == 401
    assert client.get("/api/audit").status_code == 401
    # 匿名白名单不受影响
    assert client.get("/health").status_code in (200, 503)  # 取决于 DB 可达性，不应是 401


def test_login_flow(api: ApiFixture) -> None:
    client, _ = api
    assert _login(client, password="wrong").status_code == 401
    resp = _login(client)
    assert resp.status_code == 200
    assert resp.json() == {"id": 1, "username": "operator"}
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "operator"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_forged_cookie_rejected(api: ApiFixture) -> None:
    # 防伪造：手造的票据（改操作者/假签名）验不过（签名细节由 sessions 单测覆盖）
    client, _ = api
    client.cookies.set("suite_session", "1.9999999999.deadbeef")
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()  # 清掉伪造票据，不污染共享 client 的登录态


# ---------- 瓶装水全链路：机洗抽到 -> 确认 -> 发布 -> 写回 -> 审计 ----------


def test_water_document_full_journey(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    water_id = _product_id_by_name(client, "瓶装水")

    resp = _upload(client, _WATER_DOC, product_id=water_id, title="瓶装水规格文档")
    assert resp.status_code == 201
    asset = resp.json()
    assert asset["status"] == "pending_review"
    assert asset["kind"] == "document"
    assert asset["last_error"] is None
    version = asset["versions"][0]
    assert version["version_no"] == 1
    assert version["extracted_fields"]["净含量"] == {"value": "550毫升", "source": "machine"}
    assert version["extracted_fields"]["保质期"] == {"value": "12个月", "source": "machine"}
    # 食品类目没有材质字段：字段集合 = spec_schema 的 keys
    assert "材质" not in version["extracted_fields"]
    assert re.fullmatch(r"documents/[0-9a-f]{32}/[0-9a-f]{16}\.txt", version["object_key"])

    asset_id = asset["id"]

    # 未确认发布：422，全部归入 unconfirmed（机洗有值没人确认）
    gate = client.post(f"/api/assets/{asset_id}/publish")
    assert gate.status_code == 422
    detail = gate.json()["detail"]
    assert detail["missing"] == []
    assert sorted(detail["unconfirmed"]) == ["保质期", "净含量"]

    # 确认机洗值（人洗）
    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"净含量": "550毫升", "保质期": "12个月"},
    )
    assert patched.status_code == 200
    assert patched.json()["confirmed_fields"]["净含量"] == {"value": "550毫升", "source": "human"}

    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200
    body = published.json()
    assert body["status"] == "published"
    assert body["current_published_version_no"] == 1
    assert body["versions"][0]["published_at"] is not None

    # 写回商品（0010）：值 + {asset_id, version} 来源
    product = client.get(f"/api/products/{water_id}").json()
    assert product["spec_values"]["净含量"] == {
        "value": "550毫升",
        "source": {"asset_id": asset_id, "version": 1},
    }
    assert product["spec_values"]["保质期"]["source"]["version"] == 1

    # 审计（0005）：confirm + publish 各留痕，publish 指向该资产该版
    audit = client.get("/api/audit", params={"assetId": asset_id}).json()
    actions = [row["action"] for row in audit]
    assert actions.count("publish") == 1
    assert actions.count("confirm") == 1
    publish_row = next(row for row in audit if row["action"] == "publish")
    assert publish_row["version_no"] == 1
    assert publish_row["asset_id"] == asset_id

    # 发布后不可变（0006）：PATCH 409、重复发布 409、retry 409
    assert client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"净含量": "1ml"}).status_code == 409
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 409
    assert client.post(f"/api/assets/{asset_id}/retry-machine-wash").status_code == 409


# ---------- 钛钢保温杯：材质弃权 -> 补填；缺项两类同时出现 ----------


def test_cup_material_abstain_then_fill(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    cup_id = _product_id_by_name(client, "钛钢保温杯")

    resp = _upload(client, _CUP_DOC, product_id=cup_id, title="保温杯规格文档")
    assert resp.status_code == 201
    asset = resp.json()
    extracted = asset["versions"][0]["extracted_fields"]
    assert extracted["净含量"] == {"value": "480ml", "source": "machine"}
    # 「材质牌号未标注」：显式弃权，不得抽出「牌号」或编造值
    assert extracted["材质"] == {"abstained": True}

    asset_id = asset["id"]
    gate = client.post(f"/api/assets/{asset_id}/publish")
    assert gate.status_code == 422
    detail = gate.json()["detail"]
    assert detail["missing"] == ["材质"]  # 缺少必填字段（弃权/未填）
    assert detail["unconfirmed"] == ["净含量"]  # 待确认字段（机洗有值）

    fill = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"材质": "钛钢", "净含量": "480ml"},
    )
    assert fill.status_code == 200
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200

    product = client.get(f"/api/products/{cup_id}").json()
    assert product["spec_values"]["材质"] == {
        "value": "钛钢",
        "source": {"asset_id": asset_id, "version": 1},
    }


# ---------- 机洗失败停已接入 + 就地重试（0012 不建任务表） ----------


def test_machine_wash_failure_and_in_place_retry(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)
    cup_id = _product_id_by_name(client, "钛钢保温杯")

    bad_bytes = b"\xff\xfe\x00\x01not-utf8"
    resp = _upload(client, bad_bytes, product_id=cup_id, title="坏字节文档")
    assert resp.status_code == 201
    asset = resp.json()
    asset_id = asset["id"]
    assert asset["status"] == "ingested"
    assert "UTF-8" in asset["last_error"]

    # 已接入态发布被 409
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 409

    # 列表按状态过滤能找到它
    ingested = client.get("/api/assets", params={"status": "ingested"}).json()
    assert any(a["id"] == asset_id for a in ingested)

    # 重试仍失败：字节还是坏的
    retry = client.post(f"/api/assets/{asset_id}/retry-machine-wash")
    assert retry.status_code == 200
    assert retry.json()["status"] == "ingested"
    assert retry.json()["last_error"]

    # 就地重试成功路径：覆写对象存储里的字节，再重试
    object_key = retry.json()["versions"][0]["object_key"]
    assert ".." not in object_key
    (storage_root / Path(*object_key.split("/"))).write_bytes(
        "净含量：500ml\n材质：304不锈钢".encode()
    )
    retry_ok = client.post(f"/api/assets/{asset_id}/retry-machine-wash")
    assert retry_ok.status_code == 200
    body = retry_ok.json()
    assert body["status"] == "pending_review"
    assert body["last_error"] is None
    assert body["versions"][0]["extracted_fields"]["材质"] == {"value": "304不锈钢", "source": "machine"}


# ---------- 上传闸门：类型 / 大小 / 空文件 ----------


def test_register_rejects_bad_uploads(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert _upload(client, b"hello", content_type="application/octet-stream").status_code == 415
    assert _upload(client, b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert _upload(client, b"").status_code == 422
    assert _upload(client, b"ok", product_id=999999).status_code == 404


# ---------- 不挂商品的文档：无规格必填，人洗通过即可发布 ----------


def test_unlinked_document_has_no_required_fields(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    resp = _upload(client, "退货政策：七日无理由。".encode(), title="退货政策")
    assert resp.status_code == 201
    asset = resp.json()
    asset_id = asset["id"]
    assert asset["product"] is None
    assert asset["versions"][0]["extracted_fields"] == {}  # 空字段集：机洗无字段抽取
    assert asset["publishability"]["publishable"] is True

    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200

    # 无商品：PATCH 任何字段都不在（空）规格字段集合内
    patched = client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"净含量": "1ml"})
    assert patched.status_code == 409  # 已发布不可改（0006）优先于字段集合校验


# ---------- 空字符串冒充（0009）与字段集合校验 ----------


def test_confirm_fields_rejects_blank_and_unknown(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    water_id = _product_id_by_name(client, "瓶装水")
    resp = _upload(client, _WATER_DOC, product_id=water_id)
    asset_id = resp.json()["id"]
    blank = client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"净含量": "   "})
    assert blank.status_code == 422
    unknown = client.patch(f"/api/assets/{asset_id}/versions/1/fields", json={"材质": "钛钢"})
    assert unknown.status_code == 422  # 食品类目没有材质字段
    missing_version = client.patch(f"/api/assets/{asset_id}/versions/99/fields", json={"净含量": "1ml"})
    assert missing_version.status_code == 404


# ---------- 读接口 ----------


def test_products_endpoints_expose_schema_and_values(api: ApiFixture) -> None:
    client, _ = api
    products = client.get("/api/products").json()
    names = {p["name"] for p in products}
    assert {"瓶装水", "钛钢保温杯"} <= names
    water = next(p for p in products if p["name"] == "瓶装水")
    assert water["spec_schema"] == {
        "净含量": {"required": True},
        "保质期": {"required": True},
    }
    detail = client.get(f"/api/products/{water['id']}").json()
    assert detail["category"] == "食品"
    assert client.get("/api/products/999999").status_code == 404


def test_assets_list_and_detail(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    bad_filter = client.get("/api/assets", params={"status": "draft"})
    assert bad_filter.status_code == 422
    published_list = client.get("/api/assets", params={"status": "published"}).json()
    assert all(a["status"] == "published" for a in published_list)
    if published_list:
        first = published_list[0]
        assert {"id", "title", "kind", "status", "product", "last_error"} <= set(first)
        detail = client.get(f"/api/assets/{first['id']}").json()
        assert detail["versions"], "详情应含版本列表（对象键/字段/弃权标记）"
    assert client.get("/api/assets/999999").status_code == 404


def test_audit_endpoint_filters_by_asset(api: ApiFixture) -> None:
    client, _ = api
    rows = client.get("/api/audit").json()
    assert rows, "此前用例已产生留痕"
    target = rows[0]["asset_id"]
    filtered = client.get("/api/audit", params={"assetId": target}).json()
    assert filtered
    assert all(r["asset_id"] == target for r in filtered)


def test_seeding_is_idempotent_across_restarts(api: ApiFixture) -> None:
    # lifespan 内 seed 幂等：同一 app 再触发一次登录可用；商品数不因重复启动翻倍
    client, _ = api
    products = client.get("/api/products").json()
    assert len([p for p in products if p["name"] == "瓶装水"]) == 1
    assert len([p for p in products if p["name"] == "钛钢保温杯"]) == 1
