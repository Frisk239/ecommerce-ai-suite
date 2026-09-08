"""并发收口集成测试（需真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

两独立 Session 同问同时 record_refusal_gap：部分唯一索引兜底 IntegrityError
路径，库内恰好一条 open 缺口。

第 26 刀追加（P1⑦）：ensure_mcp_operator_id 并发首插同款竞态——
operators.username 唯一约束 + SAVEPOINT 兜底（先例本文件缺口路径），
两线程同取系统操作者「mcp」行，库内恰一行、两线程同 id。
"""

import threading
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from suite_api.mcp_server import MCP_OPERATOR_USERNAME, ensure_mcp_operator_id
from suite_api.models import KnowledgeGap, Operator
from suite_api.services.knowledge_gaps import record_refusal_gap

ApiFixture = tuple[TestClient, Path]


def test_concurrent_refusal_gaps_insert_once(api: ApiFixture) -> None:
    client, _ = api
    session_factory = client.app.state.session_factory
    question = "并发同问只落一条缺口吗"
    barrier = threading.Barrier(2)
    ids: list[int] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with session_factory() as db:
                barrier.wait(timeout=5)
                gap = record_refusal_gap(db, question)
                db.commit()
                ids.append(gap.id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert errors == []
    assert len(ids) == 2
    assert ids[0] == ids[1]
    with session_factory() as db:
        count = db.scalar(
            select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.question == question)
        )
        assert count == 1


def test_concurrent_mcp_operator_first_insert_once(api: ApiFixture) -> None:
    """第 26 刀 P1⑦：首次并发 export 竞态——两线程同时 ensure 系统操作者
    「mcp」行：一插成功、一插撞 operators.username 唯一约束走 IntegrityError
    SAVEPOINT 兜底再查（先例 record_refusal_gap；不可 rollback——会丢 export
    同事务留痕）。终态：库内恰一行 mcp、两线程拿到同一 id、零未捕获异常。"""
    client, _ = api
    session_factory = client.app.state.session_factory
    barrier = threading.Barrier(2)
    ids: list[int] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with session_factory() as db:
                barrier.wait(timeout=5)
                ids.append(ensure_mcp_operator_id(db))
                db.commit()
        except BaseException as exc:  # noqa: BLE001 - 断言时展开
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert errors == []
    assert len(ids) == 2
    assert ids[0] == ids[1]
    with session_factory() as db:
        count = db.scalar(
            select(func.count()).select_from(Operator).where(
                Operator.username == MCP_OPERATOR_USERNAME
            )
        )
        assert count == 1
