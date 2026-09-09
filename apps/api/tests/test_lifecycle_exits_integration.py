"""生命周期出口三件全链集成测试（第 28 刀/ADR 0042，需真 PG，见 conftest）。

覆盖：修订换字节（新键写旧键删+机洗重算 extracted/confirmed 保留→发布 v2
指针前移）→放弃修订（版本消失、字节删、回滚/再修订解锁、audit 行）→废弃
失败资产（列表隐藏、字节清、audit 行、检索无影响）；边界：线上版换字节 409、
已发布资产废弃 409、v1 未发布新资产 discard_revision 409 但 discard 可用、
待人洗资产不可废弃；新写端点未登录 401。
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from suite_api.models import RetrievalChunk

ApiFixture = tuple[TestClient, Path]

_WATER_DOC = "【产品规格】\n净含量：550毫升\n保质期：12个月\n储存条件：常温避光".encode()
_WATER_V2_DOC = "【产品规格·修订】\n净含量：600毫升\n保质期：18个月\n储存条件：常温避光".encode()
_BAD_BYTES = b"\xff\xfe\x00\x01not-utf8"


def _login(client: TestClient, password: str = "operator123") -> Any:
    return client.post("/api/auth/login", json={"username": "operator", "password": password})


def _upload(
    client: TestClient,
    content: bytes,
    *,
    product_id: int | None = None,
    title: str | None = None,
) -> Any:
    files = {"file": ("spec.txt", content, "text/plain")}
    data: dict[str, str] = {}
    if product_id is not None:
        data["productId"] = str(product_id)
    if title is not None:
        data["title"] = title
    return client.post("/api/assets/register", files=files, data=data)


def _put_bytes(client: TestClient, asset_id: int, version_no: int, content: bytes) -> Any:
    return client.put(
        f"/api/assets/{asset_id}/versions/{version_no}/bytes",
        files={"file": ("spec.txt", content, "text/plain")},
    )


def _product_id_by_name(client: TestClient, name: str) -> int:
    products = client.get("/api/products").json()
    return next(p["id"] for p in products if p["name"] == name)


def _key_path(storage_root: Path, object_key: str) -> Path:
    return storage_root / Path(*object_key.split("/"))


def _confirm_and_publish_water(client: TestClient, *, title: str) -> dict:
    water_id = _product_id_by_name(client, "瓶装水")
    resp = _upload(client, _WATER_DOC, product_id=water_id, title=title)
    assert resp.status_code == 201
    asset_id = resp.json()["id"]
    patched = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"净含量": "550毫升", "保质期": "12个月"},
    )
    assert patched.status_code == 200
    published = client.post(f"/api/assets/{asset_id}/publish")
    assert published.status_code == 200
    return published.json()


# ---------- 鉴权 ----------


def test_lifecycle_exit_write_endpoints_require_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert _put_bytes(client, 1, 1, b"x").status_code == 401
    assert client.post("/api/assets/1/revisions/discard").status_code == 401
    assert client.post("/api/assets/1/discard").status_code == 401


# ---------- ① 修订换字节：新键写旧键删 + 机洗重算（confirmed 保留）→ 发布 v2 ----------


def test_replace_revision_bytes_reruns_wash_then_publish(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)
    published = _confirm_and_publish_water(client, title="瓶装水规格·换字节")
    asset_id = published["id"]

    opened = client.post(f"/api/assets/{asset_id}/revisions")
    assert opened.status_code == 201
    v2_old_key = opened.json()["versions"][1]["object_key"]
    assert _key_path(storage_root, v2_old_key).exists()
    # 开修订继承的 extracted 还是 v1 口径（550毫升/12个月）
    assert opened.json()["versions"][1]["extracted_fields"]["净含量"] == {
        "value": "550毫升",
        "source": "machine",
    }

    replaced = _put_bytes(client, asset_id, 2, _WATER_V2_DOC)
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["status"] == "published"  # 修订换字节不动 status/指针
    assert body["current_published_version_no"] == 1
    v2 = body["versions"][1]
    # 新对象键：旧键字节删（孤儿清理）、新键字节=上传正文
    assert v2["object_key"] != v2_old_key
    assert not _key_path(storage_root, v2_old_key).exists()
    assert _key_path(storage_root, v2["object_key"]).read_bytes() == _WATER_V2_DOC
    # 机洗按新字节重算 extracted；confirmed 保留（继承值不逼重存）
    assert v2["extracted_fields"]["净含量"] == {"value": "600毫升", "source": "machine"}
    assert v2["extracted_fields"]["保质期"] == {"value": "18个月", "source": "machine"}
    assert v2["confirmed_fields"]["净含量"] == {
        "value": "550毫升",
        "source": "human",
        "inherited": True,
    }

    # 发布 v2：指针前移，线上从 v1 切到新字节版
    published_v2 = client.post(f"/api/assets/{asset_id}/publish")
    assert published_v2.status_code == 200
    final = published_v2.json()
    assert final["current_published_version_no"] == 2
    assert final["revising"] is False
    text = client.get(f"/api/assets/{asset_id}/versions/2/text")
    assert text.status_code == 200
    assert "600毫升" in text.text


def test_replace_bytes_rejects_published_and_validates_upload(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    published = _confirm_and_publish_water(client, title="瓶装水规格·线上版禁改")
    asset_id = published["id"]
    # 线上版（v1 已发布且是指针）不可换字节
    assert _put_bytes(client, asset_id, 1, _WATER_V2_DOC).status_code == 409
    # 未知版本 404
    assert _put_bytes(client, asset_id, 99, _WATER_V2_DOC).status_code == 404

    # 上传校验对齐登记：类型/大小/空文件；待人洗 v1（未发布）是合法目标
    resp = _upload(client, "退货政策：七日无理由。".encode(), title="待人洗换字节校验")
    pending_id = resp.json()["id"]
    bad_type = client.put(
        f"/api/assets/{pending_id}/versions/1/bytes",
        files={"file": ("a.bin", b"x", "application/octet-stream")},
    )
    assert bad_type.status_code == 415
    assert _put_bytes(client, pending_id, 1, b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert _put_bytes(client, pending_id, 1, b"").status_code == 422


def test_replace_bytes_with_bad_utf8_stops_at_ingested_retryable(api: ApiFixture) -> None:
    """换上坏字节按登记同口径停已接入（可再换/重试），不死锁。"""
    client, _ = api
    _login(client)
    resp = _upload(client, "退货政策：七日无理由。".encode(), title="换坏字节")
    asset_id = resp.json()["id"]
    replaced = _put_bytes(client, asset_id, 1, _BAD_BYTES)
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["status"] == "ingested"
    assert "UTF-8" in body["last_error"]

    fixed = _put_bytes(client, asset_id, 1, "退货政策：三十天无理由。".encode())
    assert fixed.status_code == 200
    assert fixed.json()["status"] == "pending_review"
    assert fixed.json()["last_error"] is None


# ---------- ② 放弃修订：版本消失、字节删、回滚/再修订解锁、audit 行 ----------


def test_discard_revision_unlocks_rollback_and_reopen(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)
    published = _confirm_and_publish_water(client, title="瓶装水规格·放弃修订")
    asset_id = published["id"]
    assert client.post(f"/api/assets/{asset_id}/revisions").status_code == 201
    assert (
        client.patch(
            f"/api/assets/{asset_id}/versions/2/fields", json={"净含量": "600毫升"}
        ).status_code
        == 200
    )
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200  # v2 上线

    # 开 v3 修订：回滚被锁（既有闸门），放弃后解锁
    opened = client.post(f"/api/assets/{asset_id}/revisions")
    assert opened.status_code == 201
    v3_key = opened.json()["versions"][2]["object_key"]
    assert _key_path(storage_root, v3_key).exists()
    assert (
        client.post(f"/api/assets/{asset_id}/rollback", json={"version_no": 1}).status_code == 409
    )

    discarded = client.post(f"/api/assets/{asset_id}/revisions/discard")
    assert discarded.status_code == 200
    body = discarded.json()
    assert [v["version_no"] for v in body["versions"]] == [1, 2]  # v3 消失
    assert body["revising"] is False
    assert not _key_path(storage_root, v3_key).exists()  # 修订键字节删

    # 解锁实证：回滚可用、再开修订可用
    rolled = client.post(f"/api/assets/{asset_id}/rollback", json={"version_no": 1})
    assert rolled.status_code == 200
    # 此时无未发布修订：再放弃 409；随后再开修订证明修订锁也释放
    assert client.post(f"/api/assets/{asset_id}/revisions/discard").status_code == 409
    assert client.post(f"/api/assets/{asset_id}/revisions").status_code == 201

    audit = client.get("/api/audit", params={"assetId": asset_id}).json()
    discard_rows = [row for row in audit if row["action"] == "discard_revision"]
    assert len(discard_rows) == 1
    assert discard_rows[0]["version_no"] == 3
    assert discard_rows[0]["asset_id"] == asset_id


def test_discard_revision_rejects_v1_new_asset(api: ApiFixture) -> None:
    """v1 未发布的普通资产不是修订：409 引导走废弃。"""
    client, _ = api
    _login(client)
    resp = _upload(client, "退货政策：七日无理由。".encode(), title="v1 不是修订")
    asset_id = resp.json()["id"]
    rejected = client.post(f"/api/assets/{asset_id}/revisions/discard")
    assert rejected.status_code == 409
    assert "废弃" in rejected.json()["detail"]


# ---------- ③ 废弃失败资产：从未发布、字节清、列表隐藏、audit 行 ----------


def test_discard_failed_asset_hides_from_list_and_cleans_bytes(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)
    cup_id = _product_id_by_name(client, "钛钢保温杯")

    resp = _upload(client, _BAD_BYTES, product_id=cup_id, title="GBK 传错文件")
    assert resp.status_code == 201
    asset = resp.json()
    asset_id = asset["id"]
    assert asset["status"] == "ingested"
    object_key = asset["versions"][0]["object_key"]
    assert _key_path(storage_root, object_key).exists()

    discarded = client.post(f"/api/assets/{asset_id}/discard")
    assert discarded.status_code == 200
    # 字节清（storage.delete 接线）；版本行保留作审计锚
    assert not _key_path(storage_root, object_key).exists()
    assert discarded.json()["versions"][0]["version_no"] == 1

    # 列表默认隐藏：全量与各状态页签都不可见（discarded 不是第四态）
    assert all(a["id"] != asset_id for a in client.get("/api/assets").json())
    assert all(
        a["id"] != asset_id
        for a in client.get("/api/assets", params={"status": "ingested"}).json()
    )
    # 详情按 id 直达仍可看（审计锚），正文字节已删
    assert client.get(f"/api/assets/{asset_id}").status_code == 200
    assert client.get(f"/api/assets/{asset_id}/versions/1/text").status_code == 409

    # audit 留痕
    audit = client.get("/api/audit", params={"assetId": asset_id}).json()
    assert [row["action"] for row in audit] == ["discard_asset"]
    assert audit[0]["version_no"] == 1

    # 检索无影响：从未发布本就不进索引（无切块行）
    with client.app.state.session_factory() as db:
        chunks = list(
            db.scalars(
                select(RetrievalChunk).where(RetrievalChunk.asset_id == asset_id)
            )
        )
        assert chunks == []


def test_discard_asset_state_machine_boundaries(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    # 已发布资产（含曾发布过全部版本）不可废弃：权威历史不可抹
    published = _confirm_and_publish_water(client, title="瓶装水规格·发布过禁废")
    published_id = published["id"]
    assert client.post(f"/api/assets/{published_id}/discard").status_code == 409

    # 待人洗 v1 未发布：不是失败资产，不给删除退路（仍可人洗/发布）
    resp = _upload(client, "退货政策：七日无理由。".encode(), title="待人洗禁废")
    pending_id = resp.json()["id"]
    assert client.post(f"/api/assets/{pending_id}/discard").status_code == 409

    # 机洗失败 v1：discard_revision 不可用但 discard 可用（同一资产两条出口分流）
    bad = _upload(client, _BAD_BYTES, title="失败资产出口分流")
    bad_id = bad.json()["id"]
    assert client.post(f"/api/assets/{bad_id}/revisions/discard").status_code == 409
    assert client.post(f"/api/assets/{bad_id}/discard").status_code == 200
