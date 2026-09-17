"""`scripts/demo_reset.py` 的清理口径钉子（真 PG；脚本是运维工具，判据必须钉死）。

覆盖（在独立测试库上自己造数、自己验证）：
- **只清五类特征行**：空会话（无消息/工单/评分/缺口/回流锚）、探针资产
  （`mcp_registered` + `^(mcp-smoke|evidence probe)` 标题）、素材失败任务
  （status='failed'）、成片残留（非 registered 且超 1h）、录像探针
  （label 命中探针/测试特征 **且无已登记资产依赖**）。
- **不许误伤**：有消息的会话、有工单/评分/缺口的会话、非探针来源的资产、
  已 discarded 的资产（幂等）、非 failed 的素材任务、registered 的成片任务、
  新近的 planned 成片任务、**有 registered 候选的录像**（血缘证据链）。
- **幂等**：连跑两次，第二次删除/标记数都是 0。
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from suite_api.models import (
    Asset,
    ClipCandidate,
    ClipRecording,
    ComposeTask,
    MaterialTask,
    Product,
    ServiceMessage,
    ServiceSession,
    SessionRating,
)

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
        assert again["deleted_empty_sessions"] == 0
        assert again["discarded_probe_assets"] == 0
        assert all(
            value == 0
            for key, value in again.items()
            if key not in {"deleted_empty_sessions", "discarded_probe_assets"}
        )


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


# ---------- 第 111 刀新增三类判据 ----------


def _product_id(db) -> int:
    row = db.query(Product).order_by(Product.id).first()
    assert row is not None  # 种子恒有商品
    return int(row.id)


def _row_exists(db, table: str, row_id: int) -> bool:
    from sqlalchemy import text

    return (
        db.execute(
            text(f"SELECT count(*) FROM {table} WHERE id = :id"), {"id": row_id}
        ).scalar_one()
        > 0
    )


def _material(db, *, status: str) -> MaterialTask:
    task = MaterialTask(product_id=_product_id(db), status=status, title="清理判据用")
    db.add(task)
    db.commit()
    return task


def _compose(db, *, status: str, age_hours: float) -> ComposeTask:
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    task = ComposeTask(
        product_id=_product_id(db),
        status=status,
        template="station",
        timeline=[],
        duration_seconds=30.0,
        # 暂存键 NOT NULL（成片建任务即有 preview/draft）——用假键，不真写存储
        preview_object_key="compose/test-preview.mp4",
        draft_object_key="compose/test-draft.json",
        created_at=created,
    )
    db.add(task)
    db.commit()
    return task


def _recording(db, *, label: str) -> ClipRecording:
    rec = ClipRecording(label=label, object_key=f"recordings/{label}", size_bytes=1024)
    db.add(rec)
    db.commit()
    return rec


def _candidate(
    db, *, recording: ClipRecording, registered_asset_id: int | None = None
) -> ClipCandidate:
    cand = ClipCandidate(
        status="registered" if registered_asset_id else "pending",
        timecode_start="00:00:00",
        timecode_end="00:00:05",
        transcript="清理判据用候选",
        source_video_label=recording.label,
        recording_id=recording.id,
        registered_asset_id=registered_asset_id,
    )
    db.add(cand)
    db.commit()
    return cand


def test_plan_and_apply_new_residue_types(session_factory) -> None:
    """素材失败任务 / 成片残留（非 registered 且超 1h）/ 录像探针（无已登记依赖）
    三类都清；非 failed 的素材任务、registered 与新近 planned 的成片任务、
    有 registered 候选的录像一律不动。"""
    with session_factory() as db:
        failed = _material(db, status="failed")
        kept_pending_qc = _material(db, status="pending_qc")

        stale_compose = _compose(db, status="planned", age_hours=3)
        kept_registered = _compose(db, status="registered", age_hours=3)
        fresh_compose = _compose(db, status="planned", age_hours=0)

        # 探针录像（无依赖）→ 清；有 registered 候选的录像 → 留存
        probe_rec = _recording(db, label="asr_probe.mp4")
        probe_cand = _candidate(db, recording=probe_rec)
        kept_rec = _recording(db, label="live93.mp4")  # 不命中特征词
        kept_cand = _candidate(db, recording=kept_rec)
        chain_rec = _recording(db, label="acceptance-30min.mp4")  # 命中但挂 registered 候选
        chain_asset = _asset(db, source_kind="clip_pick", title="录像依赖资产")
        chain_cand = _candidate(db, recording=chain_rec, registered_asset_id=chain_asset.id)

        # 先记 id（清理后 ORM 对象会失效，expire 后读 id 也会抛 ObjectDeletedError）
        ids = {
            "failed": failed.id,
            "kept_material": kept_pending_qc.id,
            "stale_compose": stale_compose.id,
            "kept_registered": kept_registered.id,
            "fresh_compose": fresh_compose.id,
            "probe_rec": probe_rec.id,
            "probe_cand": probe_cand.id,
            "kept_rec": kept_rec.id,
            "kept_cand": kept_cand.id,
            "chain_rec": chain_rec.id,
            "chain_cand": chain_cand.id,
            "chain_asset": chain_asset.id,
        }

        plan = demo_reset.plan(db)
        assert plan["failed_material_tasks"] >= 1
        assert plan["stale_compose_tasks"] >= 1
        assert plan["probe_recordings"] >= 1
        assert plan["probe_recording_candidates"] >= 1
        assert plan["probe_recordings_retained"] >= 1

        changed = demo_reset.apply_cleanup(db)
        assert changed["deleted_failed_material_tasks"] >= 1
        assert changed["deleted_stale_compose_tasks"] >= 1
        assert changed["deleted_probe_recordings"] >= 1
        assert changed["deleted_probe_candidates"] >= 1

        db.expire_all()
        # 删除过的行用裸 SQL 查（ORM identity map 会对已删行抛 ObjectDeletedError）
        assert not _row_exists(db, "material_tasks", ids["failed"])
        assert _row_exists(db, "material_tasks", ids["kept_material"])
        assert not _row_exists(db, "compose_tasks", ids["stale_compose"])
        assert _row_exists(db, "compose_tasks", ids["kept_registered"])
        assert _row_exists(db, "compose_tasks", ids["fresh_compose"])
        assert not _row_exists(db, "clip_recordings", ids["probe_rec"])
        assert not _row_exists(db, "clip_candidates", ids["probe_cand"])
        assert _row_exists(db, "clip_recordings", ids["kept_rec"])
        assert _row_exists(db, "clip_candidates", ids["kept_cand"])
        # 血缘证据链：命中特征但有 registered 候选的录像与候选都留着
        assert _row_exists(db, "clip_recordings", ids["chain_rec"])
        assert _row_exists(db, "clip_candidates", ids["chain_cand"])
        # 中台资产（候选指向的）永不动
        assert _row_exists(db, "assets", ids["chain_asset"])

        again = demo_reset.apply_cleanup(db)
        assert again["deleted_failed_material_tasks"] == 0
        assert again["deleted_stale_compose_tasks"] == 0
        assert again["deleted_probe_recordings"] == 0
        assert again["deleted_probe_candidates"] == 0


def test_label_markers_keep_live93_and_cover_test_recordings() -> None:
    """特征词表钉死：live93（第八幕 ASR 素材）不命中；探针/测试命名命中。"""
    import re

    pattern = re.compile(demo_reset.PROBE_LABEL_SQL)
    assert not pattern.search("live93.mp4")
    for label in (".slice-test.mp4", "acceptance-30min.mp4", "rebind-320x240.mp4", "asr_probe.mp4"):
        assert pattern.search(label), label
