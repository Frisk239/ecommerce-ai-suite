"""演示库探针清理（第 61 刀；第 111 刀扩清理判据）：把审核/演示前的「探针噪音」清成干净演示态。

## 为什么

演示库长期被冒烟、审计与端到端探针写入（审计刀 12 实测：会话 122 条里 **33 条空会话**、
`audit_log` 842 条里 **export 750**、探针资产 16 条、pending 工单 18 张）——演示时客服页
与总览第一屏肉眼可见的脏。本脚本按**特征**（不是时间、不是「看着像」）清，
其余只**报告**不删。

## 清什么（五类，都要特征命中；第 111 刀从两类扩到五类）

1. **空会话**：`service_sessions` 里「没有任何消息、工单、评分、缺口、回流资产」的行
   （探针建了不用的会话就是这种）。删除是安全的：它没有任何下游事实。
2. **探针资产**：`source_kind='mcp_registered'` 且标题命中 `^(mcp-smoke|evidence probe)`
   （= 工作队列口径 `workQueue.isWorkProbe` 的同一判据）→ **置 discarded**（ADR 0042
   的既有语义：列表/检索/导出都不再出现），**不删行、不删字节**——审计留痕仍在。
3. **素材失败任务**（第 111 刀）：`material_tasks.status='failed'` 的行——进程中断/
   验收失败遗留的终态任务行，任务非中台资产（ADR 0012），清行即成。**注意**：
   第十幕「降级诚实」用 failed 素材任务讲过「素材路径无降级，失败即失败」——
   还要拿它当证据就别对演示库 `--apply`。
4. **成片任务残留**（第 111 刀）：`compose_tasks` 里 `status <> 'registered'` 且
   `created_at` 超 1h 的行（108 审计第 4 类判据）——含其 preview/draft 暂存字节
   （非中台对象，删行一并清；存储失败不挡删行）。
5. **录像探针**（第 111 刀）：`clip_recordings.label` 命中探针/测试特征词
   （探针|probe|test|acceptance|rebind；live93 不命中=第八幕 ASR 素材）**且挂的
   候选里没有已登记资产**（有 registered 候选=已产出中台资产的证据链，绝不动）
   → 删录像行与挂其上的 pending 候选；对象字节留档（红线：只删非中台行）。
   命中但有 registered 候选的录像在报告里标「留存」。

## 不清什么（只报告）

- **知识缺口**：探针问题（如「无人机多少钱？」）在演示里正是缺口闭环的素材，删了就演不出
  「拒答留缺口 → 去补 → 再问命中」。
- **工单**：附在真实会话上的工单是「转人工闭环」的证据；只有随空会话一起被删的才消失。
- **已发布资产 / 会话消息 / 评分 / 审计留痕**：一律不动（那是平台的既成事实）。
- **有 registered 候选的录像**：A-263/264/265/267 的血缘证据（candidate.registered_asset_id）。

## 用法

```bash
uv run python scripts/demo_reset.py --db postgresql://suite:suite@localhost:5433/suite            # 只报告（默认 dry-run）
uv run python scripts/demo_reset.py --db postgresql://suite:suite@localhost:5433/suite --apply    # 真清
```

**默认 dry-run、必须显式 `--apply`**：清理是不可逆的数据动作，不该「顺手跑一下就写了」。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# 两个特征判据（与产品口径同源：探针资产判据 = workQueue.isWorkProbe 的服务端镜像；
# 前端是 /i 大小写不敏感，这里用 PG 的 ~* 对齐——机器标题恒小写，但「同源」要真同源）
PROBE_TITLE_SQL = r"^(mcp-smoke|evidence probe)"
# 录像 label 的探针/测试特征（第 111 刀）：前两个与 data_health_check.PROBE_LABEL_MARKERS
# 同源（108 审计第 4 类的验收录像口径）；test/acceptance/rebind 是前几刀验收/冒烟
# 录像的命名惯例（.slice-test.mp4 / acceptance-30min.mp4 / rebind-320x240.mp4），
# 108 审计未圈而演示前清理该圈。**live93.mp4 刻意不命中**：第八幕 ASR 素材。
PROBE_LABEL_SQL = r"探针|probe|test|acceptance|rebind"
STALE_COMPOSE_HOURS = 1  # 成片任务残留阈值（108 审计第 4 类判据）
COMPOSE_DONE_STATUS = "registered"  # 成片任务的终态（非它且超 1h = 残留）
FAILED_MATERIAL_STATUS = "failed"


def _one(session: Any, sql: str, params: dict[str, Any] | None = None) -> int:
    from sqlalchemy import text

    return int(session.execute(text(sql), params or {}).scalar_one())


def _stale_compose_filter() -> str:
    """成片残留判据（两处共用同一段字面：plan 计数与 apply 删除不许漂移）。"""
    return (
        f"status <> '{COMPOSE_DONE_STATUS}'"
        f" AND created_at < now() - interval '{STALE_COMPOSE_HOURS} hour'"
    )


def plan(session: Any) -> dict[str, int]:
    """盘点可清项与噪音现状（纯读；返回计数，供 dry-run 报告与测试断言）。"""

    empty_sessions = _one(
        session,
        """
        SELECT count(*) FROM service_sessions s
        WHERE NOT EXISTS (SELECT 1 FROM service_messages m WHERE m.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM handoff_tickets t WHERE t.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM session_ratings r WHERE r.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM knowledge_gaps g WHERE g.session_id = s.id)
          AND s.registered_asset_id IS NULL
        """,
    )
    probe_assets = _one(
        session,
        """
        SELECT count(*) FROM assets
        WHERE discarded_at IS NULL
          AND source_kind = 'mcp_registered'
          AND title ~* :pattern
        """,
        {"pattern": PROBE_TITLE_SQL},
    )
    # 第 111 刀新增三类：素材失败任务 / 成片残留 / 录像探针（同判据段复用）
    failed_material = _one(
        session,
        "SELECT count(*) FROM material_tasks WHERE status = :status",
        {"status": FAILED_MATERIAL_STATUS},
    )
    stale_compose = _one(
        session, f"SELECT count(*) FROM compose_tasks WHERE {_stale_compose_filter()}"
    )
    # 录像探针：命中 label 特征且**没有 registered 候选**（有已登记资产的=证据链，留存）
    probe_recordings = _one(
        session,
        """
        SELECT count(*) FROM clip_recordings r
        WHERE r.label ~* :pattern
          AND NOT EXISTS (
              SELECT 1 FROM clip_candidates c
              WHERE c.recording_id = r.id AND c.registered_asset_id IS NOT NULL
          )
        """,
        {"pattern": PROBE_LABEL_SQL},
    )
    retained_recordings = _one(
        session,
        """
        SELECT count(*) FROM clip_recordings r
        WHERE r.label ~* :pattern
          AND EXISTS (
              SELECT 1 FROM clip_candidates c
              WHERE c.recording_id = r.id AND c.registered_asset_id IS NOT NULL
          )
        """,
        {"pattern": PROBE_LABEL_SQL},
    )
    probe_candidates = _one(
        session,
        """
        SELECT count(*) FROM clip_candidates c
        JOIN clip_recordings r ON r.id = c.recording_id
        WHERE r.label ~* :pattern AND c.registered_asset_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM clip_candidates c2
              WHERE c2.recording_id = r.id AND c2.registered_asset_id IS NOT NULL
          )
        """,
        {"pattern": PROBE_LABEL_SQL},
    )
    # 只报告（不动）的噪音：让演示者心里有数
    report = {
        "open_gaps": _one(session, "SELECT count(*) FROM knowledge_gaps WHERE status = 'open'"),
        "pending_tickets": _one(
            session, "SELECT count(*) FROM handoff_tickets WHERE status = 'pending'"
        ),
        "sessions_total": _one(session, "SELECT count(*) FROM service_sessions"),
        "audit_rows": _one(session, "SELECT count(*) FROM audit_log"),
        # 第 83 刀：空标题**已发布**资产（提示而非自动清）——可能是 golden 引用的
        # 真实资料（如退货政策资产），补齐标题即可；直接废弃会打掉评测期望。
        # 演示者照此数去看 assets 页补齐，别指望 demo_reset 清。
        "published_without_title": _one(
            session,
            "SELECT count(*) FROM assets WHERE status = 'published' AND discarded_at IS NULL"
            " AND (title IS NULL OR btrim(title) = '')",
        ),
    }
    return {
        "empty_sessions": empty_sessions,
        "probe_assets": probe_assets,
        "failed_material_tasks": failed_material,
        "stale_compose_tasks": stale_compose,
        "probe_recordings": probe_recordings,
        "probe_recording_candidates": probe_candidates,
        "probe_recordings_retained": retained_recordings,
        **report,
    }


def apply_cleanup(session: Any, storage: Any = None) -> dict[str, int]:
    """执行清理（幂等）：返回真实删除/标记的行数。

    顺序要紧：先删空会话（它的工单/评分上游都为空，FK 不会被挡），再标探针资产；
    录像先删候选再删录像行（FK）；成片任务清暂存字节在删行之前（存储失败不挡删行）。
    ``storage`` 可选（None=只删库行，暂存字节留待生命周期兜底）——演示主流程
    不传，109 式全清传 LocalDirectoryStorage。
    """
    from sqlalchemy import text

    empty = session.execute(
        text(
            """
            DELETE FROM service_sessions s
            WHERE NOT EXISTS (SELECT 1 FROM service_messages m WHERE m.session_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM handoff_tickets t WHERE t.session_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM session_ratings r WHERE r.session_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM knowledge_gaps g WHERE g.session_id = s.id)
              AND s.registered_asset_id IS NULL
            """
        )
    ).rowcount
    assets = session.execute(
        text(
            """
            UPDATE assets SET discarded_at = now()
            WHERE discarded_at IS NULL
              AND source_kind = 'mcp_registered'
              AND title ~* :pattern
            """
        ),
        {"pattern": PROBE_TITLE_SQL},
    ).rowcount
    failed_material = session.execute(
        text("DELETE FROM material_tasks WHERE status = :status"),
        {"status": FAILED_MATERIAL_STATUS},
    ).rowcount
    # 成片残留：先清 preview/draft 暂存字节（非中台对象），再删任务行
    compose_rows = list(
        session.execute(
            text(
                "SELECT id, preview_object_key, draft_object_key FROM compose_tasks"
                f" WHERE {_stale_compose_filter()}"
            )
        )
    )
    if storage is not None:
        for _task_id, preview_key, draft_key in compose_rows:
            for key in (preview_key, draft_key):
                if not key:
                    continue
                try:
                    storage.delete(key)
                except Exception:  # noqa: BLE001 - 存储侧失败不挡删行（video_compose 先例）
                    pass
    compose = session.execute(
        text(f"DELETE FROM compose_tasks WHERE {_stale_compose_filter()}")
    ).rowcount
    # 录像探针：命中 label 特征且无 registered 候选（证据链留存）。
    # 先取 id 再「先候选后录像」（FK：clip_candidates.recording_id -> clip_recordings.id）。
    probe_ids = [
        int(row[0])
        for row in session.execute(
            text(
                """
                SELECT r.id FROM clip_recordings r
                WHERE r.label ~* :pattern
                  AND NOT EXISTS (
                      SELECT 1 FROM clip_candidates c
                      WHERE c.recording_id = r.id AND c.registered_asset_id IS NOT NULL
                  )
                ORDER BY r.id
                """
            ),
            {"pattern": PROBE_LABEL_SQL},
        )
    ]
    candidates = recordings = 0
    if probe_ids:
        from sqlalchemy import bindparam

        candidates = session.execute(
            text("DELETE FROM clip_candidates WHERE recording_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": probe_ids},
        ).rowcount
        recordings = session.execute(
            text("DELETE FROM clip_recordings WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": probe_ids},
        ).rowcount
    session.commit()
    return {
        "deleted_empty_sessions": int(empty or 0),
        "discarded_probe_assets": int(assets or 0),
        "deleted_failed_material_tasks": int(failed_material or 0),
        "deleted_stale_compose_tasks": int(compose or 0),
        "deleted_probe_recordings": int(recordings or 0),
        "deleted_probe_candidates": int(candidates or 0),
    }


def _resolve_storage_root(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="演示库探针清理（默认 dry-run）")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply", action="store_true", help="真清（不传则只报告；清理不可逆）"
    )
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("STORAGE_ROOT", "./data/objects"),
        help="对象存储根（--apply 时清成片任务暂存字节用；默认 .env/STORAGE_ROOT）",
    )
    args = parser.parse_args(argv)
    if not args.db:
        print("错误：需要 --db 或 DATABASE_URL", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(args.db))
    # 审计刀 13 B 轴 P2：回显目标库（掩密码）——--db 默认吃 shell 的 DATABASE_URL，
    # 本仓 .env 写的是 5432 而演示库在 5433，指错库的清理不可察觉是运维脚枪。
    shown = re.sub(r"(://[^:/@]+:)[^@]+(@)", lambda m: m.group(1) + "***" + m.group(2), args.db)
    print(f"目标库：{shown}{'（--apply 将写库）' if args.apply else '（dry-run，只读）'}")
    with Session(engine) as session:
        before = plan(session)
        print("探针盘点（dry-run）：")
        for key, value in before.items():
            print(f"  {key}: {value}")
        if before["probe_recordings_retained"]:
            print(
                "  注："
                f"{before['probe_recordings_retained']} 条探针/测试录像有已登记资产依赖"
                "（血缘证据链）——留存不动。"
            )
        if not args.apply:
            print("\n只报告（未写库）。要清跑：--apply")
            engine.dispose()
            return 0
        storage = None
        from suite_platform.storage import LocalDirectoryStorage  # 延迟导入（dry-run 零依赖）

        storage = LocalDirectoryStorage(_resolve_storage_root(args.storage_root))
        changed = apply_cleanup(session, storage)
        after = plan(session)
    engine.dispose()
    print("\n已清理：")
    for key, value in changed.items():
        print(f"  {key}: {value}")
    print("清理后：")
    for key, value in after.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
