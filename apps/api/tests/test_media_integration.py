"""第 94b 刀：媒体引用回答 + 媒体字节端点集成测试（真 PG；ADR 0052）。

契约（施工单 Must 1/2/3/6）：
- complete 载荷**恒有** ``media_citations`` 键（无媒体=[]，不是缺键）；服务端按
  citations 命中的资产种类+对象键后缀派生 mime（模型无决定权），并随消息落库
  （重载会话由会话详情还原）；
- ``GET /api/customer/assets/{id}/media`` 双通道鉴权（操作者 cookie / 顾客令牌
  Bearer 或 ``?token=``）：图片直出（Content-Type/Content-Length 按字节）、
  视频 Range 206/416 语义、流式分块；
- **只出当前已发布指针版**：未发布（待人洗）/已废弃/非媒体资产/旧文本切片一律
  404 同一文案（不泄漏存在性）；无凭证 401；
- 响应不回对象键。

视频资产用 SQL 直插（登记端点不产 video：切片拣选才登记，需真 ffmpeg 录像——
本测试只钉「已发布视频的媒体面」，不重跑切片链路；转写走 extracted 字段，
视频发布**不读字节**，故 md5 无关的假 mp4 字节即可）。
"""

import base64
import os
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

# 1x1 真 PNG（魔数复验用，与 94a 集成测试同源）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
# 假 mp4 字节：端点不解析容器，只按字节区间出（Range 语义是纯搬运）
_MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8
_DESCRIPTION = "显示器侧面带可调节支架，正面三边窄边框"
_TRANSCRIPT = "这个杯子内胆是一体成型的 316 不锈钢，泡柠檬水也不怕腐蚀。"


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


def _exec(sql: str, params: tuple = ()) -> None:
    """写语句（无结果集）：连接上下文退出即提交。"""
    with psycopg.connect(os.environ[_URL_ENV]) as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def _put_object(storage_root: Path, key: str, data: bytes) -> None:
    target = storage_root.joinpath(*key.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _customer(client: TestClient) -> tuple[int, str]:
    created = client.post("/api/customer/sessions")
    assert created.status_code == 201, created.text
    return int(created.json()["session_id"]), str(created.json()["token"])


def _ask_customer(client: TestClient, session_id: int, token: str, question: str) -> dict:
    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": question},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    complete = [payload for event, payload in events if event == "complete"]
    assert complete, f"没有 complete 事件: {events}"
    return complete[0]


def _ask_operator(client: TestClient, question: str) -> dict:
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


def _upload_image(
    client: TestClient,
    *,
    data: bytes = _PNG,
    name: str = "frame.png",
    content_type: str = "image/png",
    title: str = "显示器商品图",
) -> dict:
    resp = client.post(
        "/api/assets/register",
        files={"file": (name, data, content_type)},
        data={"title": title},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _publish_image(client: TestClient, asset_id: int, description: str = _DESCRIPTION) -> None:
    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields", json={"图片描述": description}
    )
    assert patched.status_code == 200, patched.text
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200, published.text


def _insert_video_asset(
    storage_root: Path, *, transcript: str = _TRANSCRIPT, suffix: str = "mp4"
) -> tuple[int, str]:
    """直插一条待人洗 video 资产（v1 带 transcript 预置字段），返回 (asset_id, key)。

    登记端点不产 video（只有切片拣选会），故视频面在测试里走 SQL 建行 + 真发布
    端点（发布=切块入索引，走的是产品路径）。字节按 suffix 写进真存储。
    """
    key = f"clips/{os.urandom(8).hex()}/media.{suffix}"
    _put_object(storage_root, key, _MP4 if suffix == "mp4" else _MP4.decode("latin-1").encode())
    with psycopg.connect(os.environ[_URL_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assets (kind, status, source_kind, title) "
            "VALUES ('video', 'pending_review', 'clip_pick', %s) RETURNING id",
            ("媒体引用测试视频",),
        )
        asset_id = int(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO asset_versions (asset_id, version_no, object_key, extracted_fields,"
            " confirmed_fields) VALUES (%s, 1, %s, %s::jsonb, '{}'::jsonb) RETURNING id",
            (asset_id, key, f'{{"transcript": {{"value": "{transcript}", "source": "machine"}}}}'),
        )
        conn.commit()
    return asset_id, key


# ---------- 1. 图片：载荷带媒体引用 + 双通道端点字节一致 ----------


def test_image_hit_yields_media_citation_and_bytes_match(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)
    image = _upload_image(client)
    asset_id = int(image["id"])
    key = image["versions"][0]["object_key"]
    _publish_image(client, asset_id)
    assert _PNG == (storage_root.joinpath(*key.split("/"))).read_bytes()  # 库内字节基线

    # 顾客通道真跑：命中图片资产的回答带 media_citations（服务端定，模型无决定权）
    session_id, token = _customer(client)
    complete = _ask_customer(client, session_id, token, "有带支架的显示器吗")
    assert asset_id in [c["asset_id"] for c in complete["citations"]]
    assert {"asset_id": asset_id, "version_no": 1, "mime": "image/png"} in complete[
        "media_citations"
    ]

    # 消息落库（重载会话由会话详情还原：citations 的姊妹键）
    rows = _fetch(
        "SELECT media_citations FROM service_messages WHERE id = %s",
        (int(complete["message_id"]),),
    )
    assert rows and {"asset_id": asset_id, "version_no": 1, "mime": "image/png"} in rows[0][0]
    detail = client.get(f"/api/service/sessions/{session_id}")
    assert detail.status_code == 200
    message = next(m for m in detail.json()["messages"] if m["id"] == complete["message_id"])
    assert message["media_citations"] == complete["media_citations"]

    # 媒体字节：query 令牌通道（img/video 的 src 形态）与 Bearer 头通道同源同字节
    url = f"/api/customer/assets/{asset_id}/media"
    via_query = client.get(url, params={"token": token})
    assert via_query.status_code == 200, via_query.text
    assert via_query.content == _PNG
    assert via_query.headers["content-type"] == "image/png"
    assert via_query.headers["content-length"] == str(len(_PNG))
    assert via_query.headers["accept-ranges"] == "bytes"
    assert via_query.headers["cache-control"] == "private, no-store"
    assert "object_key" not in via_query.text  # 键不出门（响应体里也没有）
    via_header = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert via_header.status_code == 200 and via_header.content == _PNG

    # 操作者 cookie 通道（客服预览页 img 走同源 cookie）
    _login(client)
    via_cookie = client.get(url)
    assert via_cookie.status_code == 200 and via_cookie.content == _PNG


def test_media_endpoint_requires_credentials(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    image = _upload_image(client, title="鉴权钉子图")
    asset_id = int(image["id"])
    _publish_image(client, asset_id, description="带底座的显示器，支架可升降")

    client.cookies.clear()  # 确保没有操作者 cookie（模块级共享 client）
    url = f"/api/customer/assets/{asset_id}/media"
    assert client.get(url).status_code == 401
    assert client.get(url, params={"token": "not-a-token"}).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401
    # 令牌形态限定：非 Bearer 方案头不认（与顾客发问同口径）
    assert client.get(url, headers={"Authorization": "Token abc"}).status_code == 401


# ---------- 2. 视频：媒体引用 + Range（206/416） ----------


def test_video_hit_yields_playable_citation_with_range(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)
    asset_id, key = _insert_video_asset(storage_root)
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    assert (storage_root.joinpath(*key.split("/"))).read_bytes() == _MP4  # 字节原样在存储

    session_id, token = _customer(client)
    complete = _ask_customer(client, session_id, token, "内胆是什么材质，泡柠檬水会腐蚀吗？")
    assert {"asset_id": asset_id, "version_no": 1, "mime": "video/mp4"} in complete[
        "media_citations"
    ]

    url = f"/api/customer/assets/{asset_id}/media"
    full = client.get(url, params={"token": token})
    assert full.status_code == 200
    assert full.content == _MP4 and full.headers["content-type"] == "video/mp4"
    assert full.headers["content-length"] == str(len(_MP4))

    # Range：100 字节 -> 206 + Content-Range + 恰好 100 字节
    part = client.get(url, params={"token": token}, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206, part.text
    assert len(part.content) == 100 and part.content == _MP4[:100]
    assert part.headers["content-range"] == f"bytes 0-99/{len(_MP4)}"
    assert part.headers["content-length"] == "100"

    # 开放区间与末段后缀
    tail = client.get(url, params={"token": token}, headers={"Range": f"bytes={len(_MP4) - 10}-"})
    assert tail.status_code == 206 and tail.content == _MP4[-10:]
    assert tail.headers["content-range"] == f"bytes {len(_MP4) - 10}-{len(_MP4) - 1}/{len(_MP4)}"
    suffix = client.get(url, params={"token": token}, headers={"Range": "bytes=-8"})
    assert suffix.status_code == 206 and suffix.content == _MP4[-8:]

    # 416：起止越界 -> bytes */size（客户端据此知道真实长度）
    beyond = client.get(url, params={"token": token}, headers={"Range": "bytes=999999-"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == f"bytes */{len(_MP4)}"
    # 多段/坏头忽略 -> 200 全量（不假装支持 multipart）
    multi = client.get(url, params={"token": token}, headers={"Range": "bytes=0-9,20-29"})
    assert multi.status_code == 200 and multi.content == _MP4


def test_operator_channel_preview_gets_media_citations(api: ApiFixture) -> None:
    """操作者预览通道同构（同一引擎/同一载荷），媒体端点操作者 cookie 可访问。"""
    client, storage_root = api
    _login(client)
    asset_id, _ = _insert_video_asset(storage_root, transcript="钛钢保温杯今天下单还送便携杯刷。")
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    complete = _ask_operator(client, "下单送什么赠品？")
    assert {"asset_id": asset_id, "version_no": 1, "mime": "video/mp4"} in complete[
        "media_citations"
    ]
    media = client.get(f"/api/customer/assets/{asset_id}/media")
    assert media.status_code == 200 and media.headers["content-type"] == "video/mp4"


# ---------- 3. 只出已发布指针版：未发布/废弃/非媒体 404 ----------


def test_media_endpoint_refuses_unpublished_discarded_and_non_media(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    not_found = "媒体不存在"

    # 待人洗（已上传未发布）图片：操作者 cookie 也不放行（口径：未发布拒绝）
    pending = _upload_image(client, title="未发布图")
    pending_id = int(pending["id"])
    resp = client.get(f"/api/customer/assets/{pending_id}/media")
    assert resp.status_code == 404 and resp.json()["detail"] == not_found

    # 已发布图片 -> 200；置 discarded_at（数据面模拟 0042 废弃）-> 404
    published = _upload_image(client, title="废弃钉子图")
    published_id = int(published["id"])
    _publish_image(client, published_id, description="一台带支架的显示器正面图")
    assert client.get(f"/api/customer/assets/{published_id}/media").status_code == 200
    _exec("UPDATE assets SET discarded_at = now() WHERE id = %s", (published_id,))
    discarded = client.get(f"/api/customer/assets/{published_id}/media")
    assert discarded.status_code == 404 and discarded.json()["detail"] == not_found

    # 非媒体资产（已发布文档）-> 404（媒体端点不是通用文件出口）
    doc = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", "净含量：550毫升".encode(), "text/plain")},
        data={"title": "媒体端点非媒体钉子"},
    )
    assert doc.status_code in (201, 202)  # register 端点契约：新建 201（评审改精确）
    doc_id = int(doc.json()["id"])
    assert client.get(f"/api/customer/assets/{doc_id}/media").status_code == 404

    # 不存在的资产 -> 404 同文案（不区分「没有」与「不可出」）
    assert client.get("/api/customer/assets/99999999/media").status_code == 404


def test_legacy_text_clip_has_no_media(api: ApiFixture, tmp_path: Path) -> None:
    """旧切片（kind=video 但键是 .txt、字节是时间码文本）**不是媒体**：端点 404，
    问句命中也不产媒体引用（不按 kind 冒充 mp4）。"""
    client, storage_root = api
    _login(client)
    asset_id, key = _insert_video_asset(
        storage_root, transcript="欢迎来到直播间，今天介绍钛钢保温杯与便携杯刷套装。", suffix="txt"
    )
    assert key.endswith(".txt")
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    assert client.get(f"/api/customer/assets/{asset_id}/media").status_code == 404
    session_id, token = _customer(client)
    complete = _ask_customer(client, session_id, token, "直播间介绍的是哪款杯子？")
    # 命中（引用在）但不出媒体附件（字节不是可播媒体）
    assert asset_id in [c["asset_id"] for c in complete["citations"]]
    assert asset_id not in [m["asset_id"] for m in complete["media_citations"]]


# ---------- 4. 形态固定：无媒体恒 []（工具路径/无命中） ----------


def test_complete_payload_always_carries_media_citations_key(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    # 订单工具路径（不检索）：键在、值恒空列表
    complete = _ask_operator(client, "我的订单 SO-1001 到哪了？")
    assert complete["media_citations"] == []
    # 顾客通道同形状（无 gap_id 裁剪不影响媒体键）
    session_id, token = _customer(client)
    customer_complete = _ask_customer(client, session_id, token, "我的订单 SO-1001 到哪了？")
    assert customer_complete["media_citations"] == []
