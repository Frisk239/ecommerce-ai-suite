"""CSV 批量导入集成测试（需真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

第 9 刀用户路径：未登录 401 -> 登录 -> 导入 3 好 1 坏 -> 报告（created 3 条
带行号与资产 ID / skipped 1 条带行号与原因）-> 3 资产待人洗（kind=document、
来源=上传、不挂商品）-> 人洗闸门可走（PATCH 确认）-> 发布其一 ->
retrieval_chunks 命中新内容（0004：发布时切块写入检索索引，索引里只有已发布）
-> 重传同文件=新资产（登记幂等可重，报告可见）-> 缺列/超行数整批 422。
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from suite_api.models import RetrievalChunk
from suite_api.services.retrieval import retrieve

ApiFixture = tuple[TestClient, Path]

# 「批量导入锚定」为本测试独有关键词，不与其他已发布块串台（同 module 库共享）
_GOOD_CSV = (
    "title,content\n"
    "冷启动问答一,批量导入锚定：会员积分按消费1倍累计\n"
    "冷启动问答二,批量导入次条：支持七天无理由退货\n"
    ",这条没有标题会被跳过\n"
    "冷启动问答三,批量导入三条：发票支持企业抬头\n"
).encode()


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _import_csv(client: TestClient, data: bytes, filename: str = "batch.csv") -> Any:
    return client.post("/api/assets/import-csv", files={"file": (filename, data, "text/csv")})


def test_import_csv_requires_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert _import_csv(client, _GOOD_CSV).status_code == 401


def test_import_csv_full_journey(api: ApiFixture) -> None:
    client, _ = api
    _login(client)

    # 1) 导入 3 好 1 坏：报告行号/资产 ID/原因齐全
    resp = _import_csv(client, _GOOD_CSV)
    assert resp.status_code == 200
    report = resp.json()
    assert [row["row"] for row in report["created"]] == [1, 2, 4]
    assert [row["title"] for row in report["created"]] == [
        "冷启动问答一",
        "冷启动问答二",
        "冷启动问答三",
    ]
    assert all(isinstance(row["asset_id"], int) for row in report["created"])
    assert report["skipped"] == [{"row": 3, "reason": "title 为空"}]
    ids = [row["asset_id"] for row in report["created"]]

    # 2) 3 条资产全部待人洗：document / upload / 不挂商品（挂商品在详情页事后处理）
    for row in report["created"]:
        detail = client.get(f"/api/assets/{row['asset_id']}").json()
        assert detail["status"] == "pending_review", row
        assert detail["kind"] == "document"
        assert detail["source_kind"] == "upload"  # 0025：CSV 导入=上传通道的批量形态
        assert detail["product"] is None
        assert detail["last_error"] is None
        assert detail["versions"][0]["version_no"] == 1

    # 3) 人洗闸门可走（不挂商品无必填字段：确认空集即过）-> 发布其一
    first_id = ids[0]
    patched = client.patch(f"/api/assets/{first_id}/versions/1/fields", json={})
    assert patched.status_code == 200
    published = client.post(f"/api/assets/{first_id}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["current_published_version_no"] == 1

    # 未发布的另外两条仍在待人洗（逐行独立，互不牵连）
    for asset_id in ids[1:]:
        assert client.get(f"/api/assets/{asset_id}").json()["status"] == "pending_review"

    # 4) 检索索引命中新内容：发布事务切块落库，块里有导入的正文（0004/0017）
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        chunks = db.scalars(
            select(RetrievalChunk.chunk).where(
                RetrievalChunk.asset_id == first_id, RetrievalChunk.version_no == 1
            )
        ).all()
        assert chunks, "发布后应有切块入索引"
        assert any("批量导入锚定" in chunk for chunk in chunks)
        # 待人洗资产没有切块：索引里只有已发布
        unpublished = db.scalars(
            select(RetrievalChunk).where(RetrievalChunk.asset_id == ids[1])
        ).all()
        assert unpublished == []

        # 4.5) spec Must 4：retrieve 真正命中（客服/MCP 同一入口，0017）
        hits = retrieve(db, "会员积分怎么累计？")
        assert any(hit["asset_id"] == first_id for hit in hits), "导入→发布的资产应被检索命中"

    # 5) 重传同文件=再建 3 条新资产（登记幂等可重，Out：不做增量去重）
    again = _import_csv(client, _GOOD_CSV)
    assert again.status_code == 200
    again_ids = [row["asset_id"] for row in again.json()["created"]]
    assert len(again_ids) == 3
    assert set(again_ids).isdisjoint(ids)


def test_import_csv_rejects_bad_batches(api: ApiFixture) -> None:
    """整批不可受理 422：缺列（点名）/ 超行数；不产生任何资产（字节不落库）。"""
    client, _ = api
    _login(client)

    missing_column = _import_csv(client, b"name,content\na,b\n")
    assert missing_column.status_code == 422
    assert "title" in missing_column.json()["detail"]

    oversize = _import_csv(client, b"title,content\n" + b"t,c\n" * 201)
    assert oversize.status_code == 422
    assert "最多 200 行" in oversize.json()["detail"]

    empty_batch = _import_csv(client, b"title,content\n")  # 空批次不报错
    assert empty_batch.status_code == 200
    assert empty_batch.json() == {"created": [], "skipped": []}

    # 422 批次不登记任何资产：全库 document 标题不含这两批的输入
    titles = [a["title"] for a in client.get("/api/assets").json() if a["title"]]
    assert "a" not in titles


def test_import_csv_rejects_oversize_file(api: ApiFixture) -> None:
    """2MB 文件防线（评审处置，与单份登记同一把尺）：巨型文件在被行数规则
    拒绝前就已整读进内存——大小检查前置于解析。"""
    client, _ = api
    _login(client)
    huge = b"title,content\n" + b"x," + b"c" * (2 * 1024 * 1024) + b"\n"
    resp = _import_csv(client, huge)
    assert resp.status_code == 413
    assert "2MB" in resp.json()["detail"]
