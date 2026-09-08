"""并发收口集成测试（需真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

两独立 Session 同问同时 record_refusal_gap：部分唯一索引兜底 IntegrityError
路径，库内恰好一条 open 缺口。
"""

import threading
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from suite_api.models import KnowledgeGap
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
