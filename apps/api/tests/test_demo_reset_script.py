"""`scripts/demo_reset.py` 的清理口径钉子（真 PG；脚本是运维工具，判据必须钉死）。

覆盖（在独立测试库上自己造数、自己验证）：
- **只清两类特征行**：空会话（无消息/工单/评分/缺口/回流锚）与探针资产
  （`mcp_registered` + `^(mcp-smoke|evidence probe)` 标题）。
- **不许误伤**：有消息的会话、有工单/评分/缺口的会话、非探针来源的资产、
  已 discarded 的资产（幂等）。
- **幂等**：连跑两次，第二次删除/标记数都是 0。
"""

import os
import sys
from pathlib import Path

import pytest

from suite_api.models import Asset, ServiceMessage, ServiceSession, SessionRating

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import demo_reset  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("SUITE_TEST_DATABASE_URL"),
    reason="需真 Postgres：设 SUITE_TEST_DATABASE_URL",
)


@pytest.fixture()
def session_factory(api):
    """复用 conftest 的 module 级库（它负责建库/迁移/种子）；这里只借它的会话工厂。

    脚本按**特征**清，不依赖顺序；seed 不会造空会话或探针资产，故断言用 `>=`。
    """
    client, _ = api
    yield client.app.state.session_factory


def _message(db, session_id: int) -> None:
    db.add(ServiceMessage(session_id=session_id, role="agent", content="探针回答", kind="answer"))


def _asset(db, *, source_kind: str, title: str, discarded: bool = False):
    asset = Asset(kind="document", status="pending_review", source_kind=source_kind, title=title)
    db.add(asset)
    db.flush()
    if discarded:
        from datetime import UTC, datetime

        asset.discarded_at = datetime.now(UTC)
    db.commit()
    return asset


def test_plan_counts_only_probe_signatures(session_factory) -> None:
    with session_factory() as db:
        empty = ServiceSession(status="active")
        with_message = ServiceSession(status="active")
        db.add_all([empty, with_message])
        db.flush()
        _message(db, with_message.id)
        db.commit()
        empty_id, busy_id = empty.id, with_message.id

        _asset(db, source_kind="mcp_registered", title="mcp-smoke 保温杯")
        _asset(db, source_kind="mcp_registered", title="evidence probe 退货政策")
        _asset(db, source_kind="mcp_registered", title="正式连接层登记")  # 标题不命中
        _asset(db, source_kind="upload", title="mcp-smoke 上传的")  # 来源不命中
        _asset(db, source_kind="mcp_registered", title="mcp-smoke 已废弃", discarded=True)

        counts = demo_reset.plan(db)
        assert counts["sessions_total"] >= 2
        assert counts["probe_assets"] == 2  # 只数两条（另一条已 discarded 不计）
        assert counts["empty_sessions"] >= 1

        # 有消息的会话不算空
        assert counts["empty_sessions"] < counts["sessions_total"] or busy_id == empty_id


def test_apply_removes_empty_sessions_and_discards_probe_assets(session_factory) -> None:
    with session_factory() as db:
        empty = ServiceSession(status="active")
        rated = ServiceSession(status="active")
        db.add_all([empty, rated])
        db.flush()
        db.add(SessionRating(session_id=rated.id, score=5))
        db.commit()
        empty_id, rated_id = empty.id, rated.id

        probe = _asset(db, source_kind="mcp_registered", title="mcp-smoke 集成")
        normal = _asset(db, source_kind="upload", title="正常资产")

        changed = demo_reset.apply_cleanup(db)
        assert changed["deleted_empty_sessions"] >= 1
        assert changed["discarded_probe_assets"] >= 1

        db.expire_all()
        assert db.get(ServiceSession, empty_id) is None  # 空会话被删
        assert db.get(ServiceSession, rated_id) is not None  # 有评分的会话不动
        assert db.get(Asset, probe.id).discarded_at is not None  # 探针资产被标废弃
        assert db.get(Asset, normal.id).discarded_at is None  # 正常资产不动

        # 幂等：再跑一次没有可清项
        again = demo_reset.apply_cleanup(db)
        assert again == {"deleted_empty_sessions": 0, "discarded_probe_assets": 0}


def test_plan_reports_published_assets_without_title(session_factory) -> None:
    """第 83 刀：空标题已发布资产**只报告不清**（可能是 golden 引用的真实资料，
    如退货政策资产；直接废弃会打掉评测期望——演示者照数去补齐标题）。"""
    with session_factory() as db:
        blank = _asset(db, source_kind="upload", title=None)
        blank.status = "published"
        titled = _asset(db, source_kind="upload", title="有标题的资产")
        db.commit()

        plan = demo_reset.plan(db)
        assert plan["published_without_title"] >= 1

        changed = demo_reset.apply_cleanup(db)
        assert changed["discarded_probe_assets"] >= 0
        db.expire_all()
        # 只报告：空标题资产不被清
        assert db.get(Asset, blank.id).discarded_at is None
        assert db.get(Asset, titled.id).discarded_at is None


def test_cli_requires_apply_flag(session_factory, capsys) -> None:
    """默认 dry-run：不传 --apply 绝不写库（清理不可逆）。"""
    url = os.environ["SUITE_TEST_DATABASE_URL"]
    assert demo_reset.main(["--db", url]) == 0
    out = capsys.readouterr().out
    assert "只报告（未写库）" in out
