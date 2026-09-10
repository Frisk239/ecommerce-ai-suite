"""演示库探针清理（第 61 刀）：把审核/演示前的「探针噪音」清成干净演示态。

## 为什么

演示库长期被冒烟、审计与端到端探针写入（审计刀 12 实测：会话 122 条里 **33 条空会话**、
`audit_log` 842 条里 **export 750**、探针资产 16 条、pending 工单 18 张）——演示时客服页
与总览第一屏肉眼可见的脏。本脚本按**探针特征**（不是时间、不是「看着像」）清两类，
其余只**报告**不删。

## 清什么（只有这两类，都要特征命中）

1. **空会话**：`service_sessions` 里「没有任何消息、工单、评分、缺口、回流资产」的行
   （探针建了不用的会话就是这种）。删除是安全的：它没有任何下游事实。
2. **探针资产**：`source_kind='mcp_registered'` 且标题命中 `^(mcp-smoke|evidence probe)`
   （= 工作队列口径 `workQueue.isWorkProbe` 的同一判据）→ **置 discarded**（ADR 0042
   的既有语义：列表/检索/导出都不再出现），**不删行、不删字节**——审计留痕仍在。

## 不清什么（只报告）

- **知识缺口**：探针问题（如「无人机多少钱？」）在演示里正是缺口闭环的素材，删了就演不出
  「拒答留缺口 → 去补 → 再问命中」。
- **工单**：附在真实会话上的工单是「转人工闭环」的证据；只有随空会话一起被删的才消失。
- **已发布资产 / 会话消息 / 评分 / 审计留痕**：一律不动（那是平台的既成事实）。

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
import sys
from typing import Any

# 两个特征判据（与产品口径同源：探针资产判据 = workQueue.isWorkProbe 的服务端镜像）
PROBE_TITLE_SQL = r"^(mcp-smoke|evidence probe)"


def _one(session: Any, sql: str, params: dict[str, Any] | None = None) -> int:
    from sqlalchemy import text

    return int(session.execute(text(sql), params or {}).scalar_one())


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
          AND title ~ :pattern
        """,
        {"pattern": PROBE_TITLE_SQL},
    )
    # 只报告（不动）的噪音：让演示者心里有数
    report = {
        "open_gaps": _one(session, "SELECT count(*) FROM knowledge_gaps WHERE status = 'open'"),
        "pending_tickets": _one(
            session, "SELECT count(*) FROM handoff_tickets WHERE status = 'pending'"
        ),
        "sessions_total": _one(session, "SELECT count(*) FROM service_sessions"),
        "audit_rows": _one(session, "SELECT count(*) FROM audit_log"),
    }
    return {"empty_sessions": empty_sessions, "probe_assets": probe_assets, **report}


def apply_cleanup(session: Any) -> dict[str, int]:
    """执行清理（幂等）：返回真实删除/标记的行数。

    顺序要紧：先删空会话（它的工单/评分上游都为空，FK 不会被挡），再标探针资产。
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
              AND title ~ :pattern
            """
        ),
        {"pattern": PROBE_TITLE_SQL},
    ).rowcount
    session.commit()
    return {"deleted_empty_sessions": int(empty or 0), "discarded_probe_assets": int(assets or 0)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="演示库探针清理（默认 dry-run）")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--apply", action="store_true", help="真清（不传则只报告；清理不可逆）"
    )
    args = parser.parse_args(argv)
    if not args.db:
        print("错误：需要 --db 或 DATABASE_URL", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from suite_api.db import to_sqlalchemy_url

    engine = create_engine(to_sqlalchemy_url(args.db))
    with Session(engine) as session:
        before = plan(session)
        print("探针盘点（dry-run）：")
        for key, value in before.items():
            print(f"  {key}: {value}")
        if not args.apply:
            print("\n只报告（未写库）。要清跑：--apply")
            engine.dispose()
            return 0
        changed = apply_cleanup(session)
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
