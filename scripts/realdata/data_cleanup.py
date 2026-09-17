"""第 109 刀数据清理（第七阶段施工单执行器）：按 108 健康审计报告逐类落实。

## 为什么

第 108 刀产出只读体检与施工单（`docs/research/data-health-report.md`：retire 40 /
fix 32 / keep 17）；本脚本是**施工单的执行器**——每类一个独立函数、统一汇总输出，
默认 dry-run，只有显式 `--apply` 才写库。清理动作全部按既有语义：

- 资产 = **软删**（`discarded_at`，ADR 0042；审计刀 16 的孤儿弃置同形态）——
  **不删字节不删行**，列表/检索/导出不再出现，历史留痕（版本行/审计锚）仍在；
- 会话/录像 = **可 DELETE**（非中台对象；demo_reset 先例）；
- 工单 = 陈旧 pending 置 resolved（ADR 0046 两态，无 SLA）；缺口 = 能答未关的
  走**操作者通道重问一次**触发 12 刀 auto-close（`resolve_gap_answered_by_catalog`），
  元问题类**外科 resolved**（W3 已修先例）；
- 图片品牌 QID（A-483）= 走**操作者 HTTP 通道**开修订→换字节→人洗确认→发布
  （0005 发布权在人：脚本不代劳 SQL 发布）。

## 逐类执行清单（108 报告 → 109 施工单）

| 类 | 对象 | 动作 |
| --- | --- | --- |
| 图片不符无修复价值 | A-504、A-514、A-515 | retire（软删） |
| 字节重复冗余份 | A-484（同 A-483）、A-276/494/508/510（mcp 探针主组） | retire |
| 探针资产 | A-277/495/509/511 | retire |
| 英文对话存量 | A-221~A-225（ABCD/WANDS，已发布也污染检索） | retire |
| 重题副本 | A-4/A-5/A-273/A-275（净含量保 A-9）、A-272（保修保 A-270） | retire |
| 口径归一 | A-6、A-13（主份 A-479） | retire |
| 僵死素材任务 | M-9、M-10（running 停滞） | status=failed（终止） |
| 成片残留 | C-2（planned 超 1h） | 删任务行+清 preview/draft 暂存字节 |
| 验收录像 | R-5、R-6 + 挂其上的候选（CD-41） | 删行 |
| 陈旧工单 | pending 超 24h（审计 63 张） | resolved |
| 缺口池漏网 | G-16…G-92 共 22 条（能答未关） | 操作者通道重问触发 auto-close |
| 缺口池外科 | G-148（元问题，W3 已修）、G-149/G-150（108B 探针） | resolved |
| 108B 探针产物 | 会话 S-506~S-518（13 条）+ H-0121~125 + 其缺口 | 删会话与子行、缺口 resolved |
| 空会话 | S-242/244/245/352/354/357/380~384（11 条） | demo_reset 同判据 DELETE（审计原文指定工具） |
| 品牌 QID | A-483 正文「品牌：Q5019402」 | 修订换字节→「品牌：Fairphone」→发布 v2 |

## 据实偏离（施工前已确认，报告如实记）

1. **A-501/502/503 不 retire**（施工单第一段与红线）：A-501/502 是 110 刀换字节
   对象；A-503 是手册第二幕/ demo_prepare 依赖的版本指针素材（审计脚本
   DEMO_ASSET_KEEP 例外同源）——脚本标 skip 并输出说明。
2. **G-80 保留 open**：施工单要求外科 resolved，但 108 审计自己写明「先确认
   演示手册是否还依赖」——demo_prepare 检查点与手册第十一幕均依赖 G-80 open，
   故不动；缺口终态报告单独列出。
3. **108B 探针会话 13 条**（施工单记「5 会话」）：以现库实况为准（506–518，
   含点名的 H-0122~124/G-0149 与其后新增的 H-0125/G-0150）；工单随会话删除
   （handoff_tickets.session_id NOT NULL + FK，不可能保留离址的 resolved 行）。
4. **A-483 品牌标签**：Q5019402 的 Wikidata 实查标签是 **Fairphone**（mul label，
   2026-09-17 实查），不是「Fairbuds」（Fairbuds 是同 QID 下的产品条目）——
   按实查写 Fairphone 并在报告注明。

## 用法（仓库根目录）

```bash
# dry-run（默认，只读：盘点各步可清项与幂等状态）
uv run python scripts/realdata/data_cleanup.py --db postgresql://suite:suite@localhost:5433/suite

# 真清（写库；A-483 修订走操作者 HTTP 通道，需 API 在跑）
uv run python scripts/realdata/data_cleanup.py \
    --db postgresql://suite:suite@localhost:5433/suite --apply

# 重跑验证幂等：第二步应全部 0（retire 0 / fix 0）
```

清理是显式动作：**默认 dry-run，必须 `--apply`**（demo_reset 同纪律）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------- 施工单常量（唯一执行清单；每条都在 108 报告里有出处） ----------

# retire：软删（discarded_at，ADR 0042）。值=理由（报告出处类号）。
RETIRE_ASSETS: dict[int, str] = {
    # 图片-描述确认不符且无修复价值（报告第 1/3/4 类）
    504: "标题乱码 + 1x1 PNG 占位字节（第 1/3/4 类）",
    514: "VLM 真跑探针图（验收残留）",
    515: "VLM 真跑探针图2（验收残留）",
    # 字节重复冗余份（报告第 2/4 类）：mcp 探针主组 + QID 冗余份
    276: "mcp 探针资产（demo_reset 同判据）",
    277: "mcp 探针资产（evidence probe）",
    494: "mcp 探针资产（demo_reset 同判据）",
    495: "mcp 探针资产（evidence probe）",
    508: "mcp 探针资产（demo_reset 同判据）",
    509: "mcp 探针资产（evidence probe）",
    510: "mcp 探针资产（demo_reset 同判据）",
    511: "mcp 探针资产（evidence probe）",
    484: "A-483 同字节冗余份（正文同为未解析 QID）",
    # ABCD/WANDS 英文对话存量（报告第 4 类；已发布者污染检索语料）
    221: "与数码店语境不匹配的英文回流对话",
    222: "与数码店语境不匹配的英文回流对话",
    223: "与数码店语境不匹配的英文回流对话（已发布）",
    224: "与数码店语境不匹配的英文回流对话（已发布）",
    225: "与数码店语境不匹配的英文回流对话（已发布）",
    # 重题副本（报告第 6 类）：净含量保 A-9、保修保 A-270
    4: "「保温杯的净含量是多少？」重题副本（保 A-9）",
    5: "「保温杯的净含量是多少？」重题副本（保 A-9）",
    273: "「保温杯的净含量是多少？」重题副本（保 A-9）",
    275: "「保温杯的净含量是多少？」重题副本（保 A-9）",
    272: "「保温杯保修多久？」重题副本（保 A-270）",
    # 口径归一（报告第 6 类）：退货主题保 A-479（最新权威）
    6: "退货/退换冗余副本（主份 A-479）",
    13: "退货/退换冗余副本（主份 A-479）",
}

# skip：施工单第一段与红线明示不动（A-501/502 是演示幕锚，永不 retire）。
# 第 110 刀执行结果回填：A-501/502 已走操作者 HTTP 修订流换真图（501 v1→v2→v3、
# 502 v2；本清理脚本不参与）；A-503 空标题重复份已退役（110 刀同形态 SQL：
# UPDATE assets SET discarded_at=now()，见 rag-eval-report 第 110 刀节）。
SKIP_ASSETS: dict[int, str] = {
    501: "演示幕锚（110 刀已换真图 v2/v3；不 retire）",
    502: "演示幕锚（110 刀已换真图 v2；不 retire）",
    503: "已随 110 刀退役（空标题重复份）",
}

# 缺口池：能答未关 22 条（报告第 5 类 fix 清单，G-80 除外——见 GAP_HELD）。
GAP_REASK_IDS: tuple[int, ...] = (
    16, 22, 23, 25, 29, 30, 33, 41, 53, 62, 64, 74,
    75, 76, 77, 81, 83, 84, 85, 86, 87, 92,
)
# 缺口池：外科 resolved（元问题 W3 先例 + 108B 探针缺口；G-148 已 resolved 幂等）。
GAP_SURGICAL_IDS: tuple[int, ...] = (148, 149, 150)
# 保留 open：演示素材依赖（施工单要求 resolved，但 108 审计附加条件未满足）。
GAP_HELD: dict[int, str] = {
    80: "OOV 演示素材：手册第十一幕 + demo_prepare 检查点依赖 open（108 审计明示先确认）",
}

# 108B 探针产物：会话 506–518（2026-09-17 07:23–08:29 亲验窗口）+ 其后新增同族。
PROBE_SESSION_IDS: tuple[int, ...] = tuple(range(506, 519))
# 随会话删除的工单（FK：handoff_tickets.session_id NOT NULL，见 docstring 偏离 3）。
PROBE_TICKET_HINT: tuple[int, ...] = (121, 122, 123, 124, 125)

# 素材任务僵死（报告第 4 类）；终态语义词表见 services/material.TASK_STATUSES。
STALE_MATERIAL_TASK_IDS: tuple[int, ...] = (9, 10)
# 成片任务残留（planned 超 1h，报告第 4 类）——任务非中台对象（ADR 0012）。
STALE_COMPOSE_TASK_IDS: tuple[int, ...] = (2,)
# 验收录像（ASR 探针，报告第 4 类）——删录像行与挂其上的候选（CD-41）。
PROBE_RECORDING_IDS: tuple[int, ...] = (5, 6)

STALE_TICKET_HOURS = 24

# A-483 品牌 QID 修订（ADR 0005：发布权在人——脚本走操作者 HTTP 通道，不 SQL 发布）。
BRAND_FIX_ASSET_ID = 483
BRAND_FIX_QID = "Q5019402"
# Wikidata wbgetentities 实查（2026-09-17）：labels.mul = Fairphone（en/zh 无标签，
# zh-cn 别名「公平手机」）。Fairbuds 是同 QID 下的**产品**条目不是品牌。
BRAND_FIX_LABEL = "Fairphone"
BRAND_FIX_LABEL_SOURCE = "Wikidata Q5019402 mul label（2026-09-17 wbgetentities 实查）"
_BRAND_QID_LINE_RE = re.compile(r"^品牌：Q\d+\s*$", re.MULTILINE)
_BRAND_QID_ANY_RE = re.compile(r"品牌：Q\d+")


# ---------- 通用小件 ----------


@dataclass
class StepResult:
    """一步（一类）的执行结果：计划/执行数 + 明细/说明（dry-run 与 apply 共用）。"""

    key: str
    title: str
    planned: int = 0
    executed: int = 0
    details: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _compact(ids: Sequence[int]) -> str:
    """id 列表紧凑形态（连续段折叠；空=「—」）。"""
    if not ids:
        return "—"
    ordered = [int(i) for i in ids]
    parts: list[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def _rows(session: Any, sql: str, params: Mapping[str, Any] | None = None) -> list[tuple]:
    from sqlalchemy import text

    return list(session.execute(text(sql), dict(params or {})))


def _rows_in(session: Any, sql: str, ids: Sequence[int]) -> list[tuple]:
    """IN (:ids) 查询（expanding bindparam——psycopg3 下数组绑定可移植）。"""
    from sqlalchemy import bindparam, text

    return list(
        session.execute(
            text(sql).bindparams(bindparam("ids", expanding=True)),
            {"ids": [int(i) for i in ids]},
        )
    )


def _exec_in(session: Any, sql: str, ids: Sequence[int], extra: Mapping[str, Any] | None = None) -> int:
    from sqlalchemy import bindparam, text

    params: dict[str, Any] = {"ids": [int(i) for i in ids]}
    params.update(extra or {})
    result = session.execute(text(sql).bindparams(bindparam("ids", expanding=True)), params)
    return int(result.rowcount or 0)


def _one(session: Any, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    rows = _rows(session, sql, params)
    return rows[0][0] if rows else None


# ---------- 纯函数（离线可测：分组/幂等/内容修订；测试见 test_realdata_scripts） ----------


@dataclass(frozen=True)
class RetirePlan:
    """资产软删计划（四分组；纯函数输出，dry-run 与 apply 共用）。"""

    to_retire: tuple[int, ...]
    already: tuple[int, ...]  # 已废弃（幂等重跑：不计入执行数）
    missing: tuple[int, ...]  # 库里没有（不改；报出来）
    skipped: tuple[int, ...]  # 施工单明示不动（skip 留说明）


def plan_asset_retire(
    rows: Iterable[tuple[int, datetime | None]],
    *,
    retire_ids: Mapping[int, str],
    skip_ids: Mapping[int, str],
) -> RetirePlan:
    """(id, discarded_at) 行序列 → 软删计划（幂等判据：discarded_at 非空=已完成）。"""
    seen = {int(asset_id): discarded for asset_id, discarded in rows}
    return RetirePlan(
        to_retire=tuple(sorted(i for i in retire_ids if i in seen and seen[i] is None)),
        already=tuple(sorted(i for i in retire_ids if i in seen and seen[i] is not None)),
        missing=tuple(sorted(i for i in retire_ids if i not in seen)),
        skipped=tuple(sorted(skip_ids)),
    )


def stale_pending_ticket_ids(
    rows: Iterable[tuple[int, str, datetime]],
    *,
    now: datetime,
    hours: int = STALE_TICKET_HOURS,
) -> tuple[int, ...]:
    """(id, status, created_at) 行序列 → 陈旧 pending 工单 id（>hours 未处理）。

    判据与 108 审计第 7 类同源：status=pending 且 created_at < now-hours。
    纯函数吃注入的 now（测试可复现）；已 resolved 的行天然不进结果=幂等。
    """
    cutoff = now - timedelta(hours=hours)
    return tuple(
        sorted(
            int(ticket_id)
            for ticket_id, status, created_at in rows
            if status == "pending" and created_at < cutoff
        )
    )


def brand_fix_content(
    content: str, *, qid: str = BRAND_FIX_QID, label: str = BRAND_FIX_LABEL
) -> str:
    """正文「品牌：Q…」整行 → 「品牌：<label>」（其余行原样；纯函数，测试钉死）。

    只替换**整行**是该 QID 的形态（审计证据形态）；非整行形态保守不动。
    """
    replaced, count = _BRAND_QID_LINE_RE.subn(f"品牌：{label}", content)
    return replaced if count else content


def brand_fix_needed(content: str, *, qid: str = BRAND_FIX_QID) -> bool:
    """正文是否还是未解析 QID 形态（幂等判据：False=已修/不适用）。"""
    del qid  # 形态判据就是「品牌：Q…」；显式参数留给将来多 QID 演进
    return bool(_BRAND_QID_ANY_RE.search(content))


@dataclass(frozen=True)
class BrandFixPlan:
    needed: bool
    reason: str
    version_no: int | None = None


def brand_fix_plan(
    content: str, *, published_version_no: int | None, has_unpublished: bool
) -> BrandFixPlan:
    """A-483 修订前置判定（纯函数）：是否还需要换字节。

    - 正文已无 QID → 不需要（幂等）；
    - 有未发布修订 → 不需要新开（人工先收口，脚本不并发抢修订）；
    - 否则需要，附当前已发布版号。
    """
    if not brand_fix_needed(content):
        return BrandFixPlan(False, "正文已无未解析 QID（幂等：修订已完成）")
    if has_unpublished:
        return BrandFixPlan(False, "已存在未发布修订（不抢修订：先人工处理）")
    return BrandFixPlan(True, "正文仍为 QID 形态", version_no=published_version_no)


def reask_verdict(gap_id: int, *, status_after: str, answer_kind: str) -> str:
    """重问一次后的缺口归宿（纯函数：12 刀 auto-close 机制的可测外壳）。

    - status_after=resolved → 「closed」（重问触发 auto-close，收口成功）；
    - 仍 open + answer 路径 → 引擎答了但同问未收（口径差异，人工核）；
    - 仍 open + refusal/handoff → 弱命中/终拒（保留 open，如实报）。
    """
    if status_after == "resolved":
        return f"G-{gap_id} closed（重问即答，auto-close 生效）"
    if answer_kind == "answer":
        return f"G-{gap_id} 仍 open（引擎已答但同问未收口，人工核）"
    return f"G-{gap_id} 仍 open（弱命中/拒答：{answer_kind}——保留待补）"


def summarize_steps(steps: Sequence[StepResult]) -> str:
    """汇总表（每类：计划数/执行数/状态）——dry-run 与 apply 共用输出。"""
    lines = ["类别                                   计划  执行  状态"]
    for step in steps:
        if step.planned == 0:
            state = "幂等（无待清）"
        elif step.executed:
            state = "已执行"
        else:
            state = "待执行（dry-run）"
        lines.append(f"{step.title:<34} {step.planned:>4}  {step.executed:>4}  {state}")
    return "\n".join(lines)


# ---------- 库执行（每类一函数；幂等：第二次跑 executed 恒 0） ----------


def retire_assets(session: Any, *, apply: bool, now: datetime | None = None) -> StepResult:
    """资产软删（ADR 0042 形态，审计刀 16 先例；不删字节不删行）。"""
    del now  # 时间由 DB now() 统管（软删钟与库同源）
    step = StepResult("retire_assets", "retire：资产软删（discarded_at）")
    rows = _rows_in(
        session, "SELECT id, discarded_at FROM assets WHERE id IN :ids", list(RETIRE_ASSETS)
    )
    plan = plan_asset_retire(rows, retire_ids=RETIRE_ASSETS, skip_ids=SKIP_ASSETS)
    step.planned = len(plan.to_retire)
    step.details = [f"A-{asset_id} ← {RETIRE_ASSETS[asset_id]}" for asset_id in plan.to_retire]
    if plan.already:
        step.notes.append(f"已废弃（幂等）：{_compact(plan.already)}")
    if plan.missing:
        step.notes.append(f"库中不存在（未动）：{_compact(plan.missing)}")
    step.notes.append(
        "skip（施工单/红线明示不动）："
        + "；".join(f"A-{i}（{SKIP_ASSETS[i]}）" for i in plan.skipped)
    )
    if apply and plan.to_retire:
        step.executed = _exec_in(
            session,
            "UPDATE assets SET discarded_at = now()"
            " WHERE discarded_at IS NULL AND id IN :ids",
            plan.to_retire,
        )
        session.commit()
    return step


def resolve_stale_tickets(session: Any, *, apply: bool, now: datetime | None = None) -> StepResult:
    """陈旧 pending 工单 resolved（报告第 7 类：两态无 SLA，清与否由本刀定）。"""
    step = StepResult("stale_tickets", "fix：陈旧 pending 工单 resolved（>24h）")
    rows = [
        (int(row[0]), str(row[1]), row[2])
        for row in _rows(
            session, "SELECT id, status, created_at FROM handoff_tickets WHERE status = 'pending'"
        )
    ]
    ids = stale_pending_ticket_ids(rows, now=now or datetime.now(UTC))
    step.planned = len(ids)
    if ids:
        step.details.append(f"H-* 陈旧 pending：{_compact(ids)}")
    if apply and ids:
        step.executed = _exec_in(
            session,
            "UPDATE handoff_tickets SET status = 'resolved', resolved_at = now(),"
            " updated_at = now() WHERE status = 'pending' AND id IN :ids",
            ids,
        )
        session.commit()
    return step


def resolve_gaps_surgically(session: Any, *, apply: bool) -> StepResult:
    """缺口外科 resolved（元问题 W3 先例 + 108B 探针缺口；G-80 保留见 notes）。"""
    step = StepResult("gaps_surgical", "fix：缺口外科 resolved（G-148/149/150）")
    ids = list(GAP_SURGICAL_IDS)
    rows = [
        (int(gid), str(status))
        for gid, status in _rows_in(session, "SELECT id, status FROM knowledge_gaps WHERE id IN :ids", ids)
    ]
    open_ids = tuple(sorted(gid for gid, status in rows if status == "open"))
    already = tuple(sorted(gid for gid, status in rows if status != "open"))
    step.planned = len(open_ids)
    if already:
        step.notes.append(f"已非 open（幂等）：{_compact(already)}")
    missing = tuple(sorted(set(ids) - {gid for gid, _ in rows}))
    if missing:
        step.notes.append(f"库中不存在（未动）：{_compact(missing)}")
    if apply and open_ids:
        step.executed = _exec_in(
            session,
            "UPDATE knowledge_gaps SET status = 'resolved', resolved_at = now()"
            " WHERE status = 'open' AND id IN :ids",
            open_ids,
        )
        session.commit()
    step.details.extend(
        f"G-{gid} 保留 open（held，不在本刀收口）：{reason}"
        for gid, reason in sorted(GAP_HELD.items())
    )
    return step


def _purge_probe_session(db: Any, session_id: int) -> None:
    """清掉重问探针会话及其子行（工单/评分/消息；缺口引用置 NULL 保 resolved 语义）。"""
    for table in ("handoff_tickets", "session_ratings", "service_messages"):
        db.execute(
            _text(f"DELETE FROM {table} WHERE session_id = :sid"), {"sid": int(session_id)}
        )
    db.execute(
        _text(
            "UPDATE knowledge_gaps SET status = 'resolved',"
            " resolved_at = COALESCE(resolved_at, now()), session_id = NULL"
            " WHERE session_id = :sid"
        ),
        {"sid": int(session_id)},
    )
    db.execute(_text("DELETE FROM service_sessions WHERE id = :sid"), {"sid": int(session_id)})
    db.commit()


def _text(sql: str) -> Any:
    from sqlalchemy import text

    return text(sql)


def _ask_gap_once(db: Any, question: str) -> str:
    """操作者通道重问一次（直调引擎 run_ask；探针会话即时建即时清，零残留）。

    施工单明示「脚本调 run_ask 或直调 chat_engine」：引擎语义与控制器同源
    （0021 同一引擎），不依赖 API 进程在跑。返回 agent 消息 kind。
    """
    from suite_api.models import ServiceSession
    from suite_api.services.chat_engine import run_ask

    session = ServiceSession(status="active")  # 无 customer_token = 操作者来源
    db.add(session)
    db.commit()
    db.refresh(session)
    try:
        outcome = asyncio.run(run_ask(db, session, question))
        db.commit()
        return str(outcome.answer.kind)
    finally:
        _purge_probe_session(db, int(session.id))


def reask_gaps(
    session: Any,
    *,
    apply: bool,
    ask: Callable[[Any, str], str] | None = None,
) -> StepResult:
    """缺口池漏网：逐条重问触发 auto-close（12 刀机制）；弱命中如实保留 open。"""
    step = StepResult("gaps_reask", "fix：缺口池漏网重问（auto-close）")
    ask = ask or _ask_gap_once
    ids = list(GAP_REASK_IDS)
    rows = [
        (int(gid), str(question), str(status))
        for gid, question, status in _rows_in(
            session, "SELECT id, question, status FROM knowledge_gaps WHERE id IN :ids", ids
        )
    ]
    open_rows = [(gid, question) for gid, question, status in rows if status == "open"]
    already = tuple(sorted(gid for gid, _, status in rows if status != "open"))
    step.planned = len(open_rows)
    if already:
        step.notes.append(f"已 resolved（幂等）：{_compact(already)}")
    missing = tuple(sorted(set(ids) - {gid for gid, _, _ in rows}))
    if missing:
        step.notes.append(f"库中不存在（未动）：{_compact(missing)}")
    if not apply:
        step.details.extend(f"G-{gid} 待重问：{question}" for gid, question in open_rows)
        return step
    for gap_id, question in open_rows:
        try:
            kind = ask(session, question)
        except Exception as exc:  # noqa: BLE001 - 单条重问失败不中断整轮（如实记，保 open）
            step.details.append(f"G-{gap_id} 重问异常（保 open）：{type(exc).__name__}")
            continue
        status_after = str(_one(session, "SELECT status FROM knowledge_gaps WHERE id = :gid", {"gid": gap_id}))
        step.details.append(reask_verdict(gap_id, status_after=status_after, answer_kind=kind))
        if status_after == "resolved":
            step.executed += 1
    return step


def clean_empty_sessions(session: Any, *, apply: bool) -> StepResult:
    """空会话（报告第 7 类）：复用 demo_reset 同判据（审计原文指定跑 demo_reset）。

    无消息/工单/评分/缺口/回流锚的会话没有任何下游事实，删除是安全的
    （demo_reset 第 61 刀判据）；幂等：已清后 planned=0。
    """
    scripts_dir = str(REPO_ROOT / "scripts")  # demo_reset 住在 scripts/ 根
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import demo_reset

    step = StepResult("empty_sessions", "retire：空会话（demo_reset 同判据）")
    step.planned = int(demo_reset.plan(session)["empty_sessions"])
    if apply and step.planned:
        changed = demo_reset.apply_cleanup(session)
        step.executed = int(changed["deleted_empty_sessions"])
        step.details.append(
            f"DELETE 空会话 {step.executed} 条（demo_reset.apply_cleanup 同判据）"
        )
    return step


def clean_probe_sessions(session: Any, *, apply: bool) -> StepResult:
    """108B 探针产物：删会话与子行；缺口 resolved 后 session_id 置 NULL（FK 让路）。"""
    step = StepResult("probe_sessions", "retire：108B 探针会话 + 工单 + 缺口")
    ids = list(PROBE_SESSION_IDS)
    present = tuple(
        sorted(int(sid) for (sid,) in _rows_in(session, "SELECT id FROM service_sessions WHERE id IN :ids", ids))
    )
    step.planned = len(present)
    step.notes.append(f"目标会话（108B 亲验窗口 2026-09-17）：{_compact(present)}")
    absent = tuple(sorted(set(ids) - set(present)))
    if absent:
        step.notes.append(f"库中不存在（已清/未建）：{_compact(absent)}")
    step.details.extend(
        f"H-{ticket_id:04d} 随会话删除（FK：handoff_tickets.session_id NOT NULL）"
        for ticket_id in PROBE_TICKET_HINT
    )
    if not apply or not present:
        return step
    gap_ids = tuple(
        sorted(
            int(gid)
            for (gid,) in _rows_in(
                session, "SELECT id FROM knowledge_gaps WHERE session_id IN :ids", list(present)
            )
        )
    )
    if gap_ids:
        step.details.append(
            f"缺口 resolved + session_id 置 NULL（{_compact(gap_ids)}）"
        )
    _exec_in(
        session,
        "UPDATE knowledge_gaps SET status = 'resolved',"
        " resolved_at = COALESCE(resolved_at, now()), session_id = NULL"
        " WHERE session_id IN :ids",
        list(present),
    )
    deleted: dict[str, int] = {}
    for table in ("handoff_tickets", "session_ratings", "service_messages"):
        deleted[table] = _exec_in(session, f"DELETE FROM {table} WHERE session_id IN :ids", list(present))
    deleted["service_sessions"] = _exec_in(
        session, "DELETE FROM service_sessions WHERE id IN :ids", list(present)
    )
    session.commit()
    step.executed = deleted["service_sessions"]
    step.details.append(
        "删除行数：" + "、".join(f"{table}={count}" for table, count in deleted.items())
    )
    return step


def fail_stale_material_tasks(session: Any, *, apply: bool) -> StepResult:
    """僵死素材任务终止（running → failed + last_error；报告第 4 类补充发现）。"""
    step = StepResult("material_tasks", "retire：僵死素材任务终止（M-9/M-10）")
    ids = list(STALE_MATERIAL_TASK_IDS)
    rows = list(
        _rows_in(session, "SELECT id, status FROM material_tasks WHERE id IN :ids", ids)
    )
    running = tuple(sorted(int(tid) for tid, status in rows if status == "running"))
    others = tuple(sorted(int(tid) for tid, status in rows if status != "running"))
    step.planned = len(running)
    if others:
        step.notes.append(f"非 running（未动，幂等）：{_compact(others)}")
    missing = tuple(sorted(set(ids) - {int(tid) for tid, _ in rows}))
    if missing:
        step.notes.append(f"库中不存在（未动）：{_compact(missing)}")
    if apply and running:
        step.executed = _exec_in(
            session,
            "UPDATE material_tasks SET status = 'failed', updated_at = now(), last_error = :err"
            " WHERE status = 'running' AND id IN :ids",
            running,
            {"err": "第 109 刀清理：进程中断遗留的 running（108 审计第 4 类僵死任务）"},
        )
        session.commit()
    return step


def purge_stale_compose_tasks(session: Any, storage: Any, *, apply: bool) -> StepResult:
    """成片任务残留：删任务行 + 清 preview/draft 暂存字节（compose/ 前缀，非资产）。"""
    step = StepResult("compose_tasks", "retire：成片任务残留（C-2）")
    ids = list(STALE_COMPOSE_TASK_IDS)
    rows = [
        (int(r[0]), str(r[1]), str(r[2]), str(r[3]))
        for r in _rows_in(
            session,
            "SELECT id, status, preview_object_key, draft_object_key FROM compose_tasks"
            " WHERE id IN :ids",
            ids,
        )
    ]
    planned = [(tid, status, preview, draft) for tid, status, preview, draft in rows if status == "planned"]
    done = tuple(sorted(tid for tid, status, _, _ in rows if status != "planned"))
    step.planned = len(planned)
    if done:
        step.notes.append(f"非 planned（未动，幂等）：{_compact(done)}")
    missing = tuple(sorted(set(ids) - {tid for tid, _, _, _ in rows}))
    if missing:
        step.notes.append(f"库中不存在（未动）：{_compact(missing)}")
    if apply and planned:
        for task_id, _status, preview_key, draft_key in planned:
            for key in (preview_key, draft_key):
                try:
                    storage.delete(key)  # 失败不挡删行：存储侧生命周期兜底（video_compose 先例）
                except Exception as exc:  # noqa: BLE001
                    step.notes.append(f"C-{task_id} 暂存清理失败（不挡）：{type(exc).__name__}")
            session.execute(_text("DELETE FROM compose_tasks WHERE id = :tid"), {"tid": task_id})
            step.details.append(f"C-{task_id} 删任务行 + preview/draft 暂存字节")
        session.commit()
        step.executed = len(planned)
    return step


def purge_probe_recordings(session: Any, *, apply: bool) -> StepResult:
    """验收录像 R-5/R-6：删录像行与挂其上的候选（CD-41）；对象字节留档不删。"""
    step = StepResult("recordings", "retire：ASR 验收探针录像（R-5/R-6）")
    ids = list(PROBE_RECORDING_IDS)
    present = [
        (int(rid), str(label))
        for rid, label in _rows_in(session, "SELECT id, label FROM clip_recordings WHERE id IN :ids", ids)
    ]
    step.planned = len(present)
    step.details.extend(f"R-{rid}（{label}）删录像行 + 挂其上的候选" for rid, label in present)
    missing = tuple(sorted(set(ids) - {rid for rid, _ in present}))
    if missing:
        step.notes.append(f"库中不存在（已清，幂等）：{_compact(missing)}")
    if apply and present:
        delete_ids = [rid for rid, _ in present]
        candidates = _exec_in(
            session, "DELETE FROM clip_candidates WHERE recording_id IN :ids", delete_ids
        )
        recordings = _exec_in(session, "DELETE FROM clip_recordings WHERE id IN :ids", delete_ids)
        session.commit()
        step.executed = recordings
        step.details.append(
            f"删除行数：clip_recordings={recordings}、clip_candidates={candidates}"
            "（对象字节留档：红线只许删非中台行）"
        )
    return step


# ---------- A-483 品牌 QID 修订（操作者 HTTP 通道） ----------


def fix_brand_qid_over_http(
    *,
    base_url: str,
    username: str,
    password: str,
    storage: Any,
    apply: bool,
    client: Any | None = None,
) -> StepResult:
    """A-483：登录 → 开修订 → 换正文（品牌：Fairphone）→ 人洗确认 → 发布。

    走真端点（0005 发布权在人 / 0010 写回；confirm/publish 双审计行由端点自己写）
    ——脚本只用操作者身份调用，不代行 SQL 发布。幂等：正文已无 QID 即跳过；
    dry-run 只做只读 GET 与计划输出。
    """
    import httpx

    step = StepResult("brand_fix", "fix：A-483 品牌 QID 修订（→Fairphone）")
    own_client = client is None
    http = client or httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=False)
    try:
        # 资产详情是操作者面（401 未登录）：先取会话 cookie（login 无库写入，
        # 只发签名 cookie——dry-run 也走，读判据才拿得到）。
        login = http.post("/api/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            step.notes.append(f"登录失败：HTTP {login.status_code}（跳过，不代行发布）")
            return step
        detail = http.get(f"/api/assets/{BRAND_FIX_ASSET_ID}")
        if detail.status_code != 200:
            step.notes.append(
                f"API 不可达/资产读失败：HTTP {detail.status_code}（跳过；--api-base 或另跑）"
            )
            return step
        body = detail.json()
        versions = body.get("versions") or []
        # 出口契约：AssetDetail 带 ``current_published_version_no``（版本号，不是
        # version id），versions 项也没有 id——匹配键必须用 version_no（109 实施
        # 教训：用 get("id") 匹配会让 None==None 命中首版，第二轮多开一次空修订）。
        published = next(
            (v for v in versions if v.get("version_no") == body.get("current_published_version_no")),
            None,
        )
        published_no = int(published["version_no"]) if published else None
        has_unpublished = any(not v.get("published_at") for v in versions)
        content = ""
        if published is not None and published.get("object_key"):
            content = storage.get_bytes(str(published["object_key"])).decode("utf-8", errors="replace")
        plan = brand_fix_plan(
            content, published_version_no=published_no, has_unpublished=has_unpublished
        )
        step.notes.append(plan.reason)
        if not plan.needed:
            return step
        step.planned = 1
        if not apply:
            step.details.append(
                f"计划：开修订 v{plan.version_no}→v{int(plan.version_no or 0) + 1}，换字节"
                f"「品牌：{BRAND_FIX_QID}→{BRAND_FIX_LABEL}」（{BRAND_FIX_LABEL_SOURCE}）"
                "→人洗 confirm 品牌→publish"
            )
            return step
        login = http.post("/api/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            step.notes.append(f"登录失败：HTTP {login.status_code}（跳过，不代行发布）")
            return step
        revision = http.post(f"/api/assets/{BRAND_FIX_ASSET_ID}/revisions")
        if revision.status_code != 201:
            step.notes.append(f"开修订失败：HTTP {revision.status_code} {revision.text[:120]}")
            return step
        draft = next(
            (v for v in (revision.json().get("versions") or []) if not v.get("published_at")), None
        )
        if draft is None:
            step.notes.append("开修订响应无未发布版本（异常，停止）")
            return step
        version_no = int(draft["version_no"])
        upload = http.put(
            f"/api/assets/{BRAND_FIX_ASSET_ID}/versions/{version_no}/bytes",
            files={"file": ("a483-brand.txt", brand_fix_content(content).encode("utf-8"), "text/plain")},
        )
        if upload.status_code != 200:
            step.notes.append(f"换字节失败：HTTP {upload.status_code} {upload.text[:120]}")
            return step
        confirm = http.patch(
            f"/api/assets/{BRAND_FIX_ASSET_ID}/versions/{version_no}/fields",
            json={"品牌": BRAND_FIX_LABEL},
        )
        if confirm.status_code != 200:
            step.notes.append(f"人洗 confirm 失败：HTTP {confirm.status_code} {confirm.text[:120]}")
            return step
        publish = http.post(f"/api/assets/{BRAND_FIX_ASSET_ID}/publish")
        if publish.status_code != 200:
            step.notes.append(f"发布失败：HTTP {publish.status_code} {publish.text[:120]}")
            return step
        step.executed = 1
        step.details.append(
            f"A-{BRAND_FIX_ASSET_ID}：v{published_no}→v{version_no} 换字节"
            f"「品牌：{BRAND_FIX_QID}→{BRAND_FIX_LABEL}」+ confirm + publish（双审计行）"
        )
        return step
    finally:
        if own_client:
            http.close()


# ---------- 汇总渲染与主流程 ----------


def print_step(step: StepResult, *, apply: bool) -> None:
    if step.planned == 0:
        state = "幂等（无待清）"
    elif step.executed:
        state = "已执行"
    else:
        state = "待执行（dry-run）" if not apply else "未执行（见说明）"
    print(f"\n[{step.key}] {step.title}")
    print(f"  计划 {step.planned} 条 · 执行 {step.executed} 条 · {state}")
    for line in step.notes:
        print(f"  · {line}")
    for line in step.details:
        print(f"    - {line}")


def run_cleanup(
    session: Any,
    storage: Any,
    *,
    apply: bool,
    api_base: str,
    operator_user: str,
    operator_password: str,
) -> list[StepResult]:
    """按类执行（顺序要紧：先软删/收口，再删会话与任务，最后 HTTP 修订）。"""
    return [
        retire_assets(session, apply=apply),
        resolve_stale_tickets(session, apply=apply),
        resolve_gaps_surgically(session, apply=apply),
        reask_gaps(session, apply=apply),
        clean_probe_sessions(session, apply=apply),
        clean_empty_sessions(session, apply=apply),
        fail_stale_material_tasks(session, apply=apply),
        purge_stale_compose_tasks(session, storage, apply=apply),
        purge_probe_recordings(session, apply=apply),
        fix_brand_qid_over_http(
            base_url=api_base,
            username=operator_user,
            password=operator_password,
            storage=storage,
            apply=apply,
        ),
    ]


def load_env_file(path: Path) -> int:
    """极简 .env 解析（同 data_health_check.load_env_file 口径，标准库）。"""
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _resolve_storage_root(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="第 109 刀数据清理（默认 dry-run）")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"), help="Postgres URL（必填）")
    parser.add_argument("--apply", action="store_true", help="真清（不传则只报告）")
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("STORAGE_ROOT", "./data/objects"),
        help="对象存储根（默认 .env/STORAGE_ROOT 或 ./data/objects）",
    )
    parser.add_argument(
        "--api-base", default="http://localhost:8000", help="操作者 API 基址（A-483 修订）"
    )
    parser.add_argument("--operator-user", default="operator", help="操作者用户名")
    parser.add_argument(
        "--operator-password",
        default=os.environ.get("OPERATOR_PASSWORD", "operator123"),
        help="操作者密码（默认 .env/OPERATOR_PASSWORD）",
    )
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"), help=".env 路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # 顺序要紧（demo_reset 脚枪同款）：--db 默认只吃**调用方环境**里的
    # DATABASE_URL，不吃 .env 里的 DATABASE_URL——本仓 .env 写 5432 而演示库在
    # 5433，静默指错库的清理不可察觉；.env 只提供操作者密码/存储根等默认值。
    env_db = os.environ.get("DATABASE_URL")
    loaded = load_env_file(Path(args.env_file))
    if args.db is None:
        args.db = env_db
    if not args.db:
        print("错误：需要 --db（或调用方环境显式设 DATABASE_URL）", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from suite_api.db import check_database, to_sqlalchemy_url
    from suite_platform.storage import LocalDirectoryStorage

    if not check_database(args.db):
        print(f"错误：连不上库 {args.db}", file=sys.stderr)
        print(
            "提示：compose 数据库的宿主端口是 5433——"
            "--db postgresql://suite:suite@localhost:5433/suite",
            file=sys.stderr,
        )
        return 2
    shown = re.sub(r"(://[^:/@]+:)[^@]+(@)", lambda m: m.group(1) + "***" + m.group(2), args.db)
    print(f"目标库：{shown}{'（--apply 将写库）' if args.apply else '（dry-run，只读）'}")
    if loaded:
        print(f"已从 .env 载入 {loaded} 个环境变量（操作者密码/LLM 等默认值）")
    storage_root = _resolve_storage_root(args.storage_root)
    print(f"对象存储根：{storage_root}")

    engine = create_engine(to_sqlalchemy_url(args.db))
    storage = LocalDirectoryStorage(storage_root)
    try:
        with Session(engine) as session:
            steps = run_cleanup(
                session,
                storage,
                apply=args.apply,
                api_base=args.api_base,
                operator_user=args.operator_user,
                operator_password=args.operator_password,
            )
    finally:
        engine.dispose()

    for step in steps:
        print_step(step, apply=args.apply)
    print("\n汇总（计划/执行）：")
    print(summarize_steps(steps))
    if not args.apply:
        print("\n只报告（未写库）。要清跑：--apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
