"""操作者仪表路由（第 43 刀）：一次请求喂三处（侧栏角标 / 总览趋势条 / 反馈汇总）。

全部指标由现有表现场聚合，**无新表、无迁移**（ADR 口径：仪表是派生视图，
不是第三个中台对象）。三条口径由 Owner 裁决钉死：

- 拒答（``refusal``）与转人工（``handoff``）分开报数——语义不同（无证据 vs
  显式要人/工具失败），且第 42 刀刚把 handoff 抬成一等公民。
- 引用覆盖率 = 有引用的 ``answer`` / 全部 ``answer``，同时下发分子分母供 UI
  标注口径；分母是「回答」，模板/工具回答也是 ``answer``（``citations=[]``），
  故它**不是** RAG 准确率。分子为 0 时返回 ``None``（不除零、不谎报 0%）。
- 近 7 日 = 含今天在内的 7 个自然日（today-6 … today，UTC，零填充升序）。

分桶实现取「拉窗口行到 Python 归桶」（同 ``routes/service.py:list_sessions`` 风
格）而非 ``date_trunc``：演示规模下窗口行量小，Python 归桶让「7 天零填充 + UTC
天」的唯一口径集中在本模块，不依赖 DB 时区设置；唯一下推到 SQL 的是被踩谓词
``feedback @> '{"helpful": false}'``（NULL 安全、键缺失安全，禁止直接下标）。
"""

import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import (
    Asset,
    HandoffTicket,
    KnowledgeGap,
    MaterialTask,
    Operator,
    ServiceMessage,
    ServiceSession,
    SessionRating,
)
from suite_api.services.machine_wash import redact_contact
from suite_api.services.triage import triage_asset_ids

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Owner 裁决 3：固定 7 个自然日（含今天）；不做自定义时间窗（本刀 Out）
WINDOW_DAYS = 7
# 反馈汇总取 top 3（Owner 裁决 8：它是行摘要，不是磁贴网格）
FEEDBACK_ASSET_LIMIT = 3
# CSAT（第 48 刀）：最近留言条数与单条截断（它是行摘要，不是评论区）
CSAT_COMMENT_LIMIT = 3
CSAT_COMMENT_MAX_CHARS = 60
# 分值档位（与 routes/customer.RATING_SCORES 同集合；分布恒给全部档位，含 0）
RATING_SCORES = (1, 2, 3, 4, 5)
# 被踩谓词（裁决：NULL 安全 + 键可能缺失）——纯 dict 字面量，由 JSONB 包含运算
# 下推；feedback 为 NULL / 键缺失 / helpful=true 都不命中
_THUMBS_DOWN = {"helpful": False}


# ---------- 响应模型（内联，照 routes/knowledge_gaps.py 先例） ----------


class StatsDaily(BaseModel):
    date: str  # YYYY-MM-DD（UTC）
    sessions: int
    refusals: int
    thumbs_down: int


class StatsBadges(BaseModel):
    """侧栏角标（Owner 裁决 5：0 时前端隐藏，后端如实给 0）。"""

    open_gaps: int  # knowledge_gaps.status='open'
    pending_qc: int  # material_tasks.status='pending_qc'
    open_tickets: int  # handoff_tickets.status='pending'


class StatsFeedbackAsset(BaseModel):
    """被踩最多的资产：citations 里出现该资产 id 的被踩消息数。

    title 来自 assets.title，资产缺失（废弃/删除）时为 None——不编造标题。
    """

    asset_id: int
    title: str | None
    count: int


class StatsCsat(BaseModel):
    """第 48 刀：CSAT（会话级 1–5 星，近 7 日窗与其余口径一致）。

    average 无样本时 None（不除零、不谎报）；distribution 是 1–5 各档计数
    （均分**必须**配分布看——4.3 可能是 5+5+3，也可能是 5+5+5+2）。
    recent_comments 是最近 3 条留言，**已掩码 + 截断 60 字**（0038：库内原文、
    出口必掩——仪表是出口）。
    """

    ratings_last_7d: int
    average_last_7d: float | None
    distribution: dict[str, int]  # {"1": n, … "5": n}，键是字符串（JSON 口径）
    recent_comments: list[str]


class StatsOverview(BaseModel):
    window_days: int
    daily: list[StatsDaily]  # 恰好 window_days 条，日期升序、零填充
    sessions_last_7d: int
    refusals_last_7d: int
    handoffs_last_7d: int
    thumbs_down_last_7d: int
    gaps_opened_last_7d: int
    gaps_resolved_last_7d: int
    answers_last_7d: int
    answers_with_citations_last_7d: int
    # 分母为 0 或分子为 0 时为 None（不除零、不谎报准确率）
    citation_rate_last_7d: float | None
    badges: StatsBadges
    feedback_assets: list[StatsFeedbackAsset]
    csat: StatsCsat


# ---------- 窗口与归桶辅助 ----------


def window_start(now: datetime) -> datetime:
    """近 7 日窗口起点：today-6 的 00:00 UTC（含今天共 7 个自然日）。

    先减 6 天再截断到天，而不是截断后减 6：跨月末/年末都得到同一个「今天往前
    数第 7 天」，不出现 8 个半截桶（Owner 裁决 3）。
    """
    return (now - timedelta(days=WINDOW_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def window_dates(since: datetime) -> list[date]:
    """窗口内 7 个 UTC 自然日，升序（since 已是零点，逐日加 i 天即可）。"""
    return [(since + timedelta(days=i)).date() for i in range(WINDOW_DAYS)]


def _utc_day(moment: datetime) -> date:
    """归一化到 UTC 天：DB 列虽是 timestamptz（实例为 UTC），显式 astimezone
    避免会话时区差异把行归错桶。"""
    return moment.astimezone(UTC).date()


def _empty_daily(day: date) -> StatsDaily:
    return StatsDaily(date=day.isoformat(), sessions=0, refusals=0, thumbs_down=0)


def _build_csat(rows: list[tuple[int, str | None, datetime]]) -> StatsCsat:
    """近 7 日评分行 -> CSAT 段（纯函数，便于单测）。

    行序按 created_at 降序（SQL 已排）——最近留言取前 N 条即可，不重排。

    **越界分三处口径统一地当它不存在**（条数 / 均值 / 分布）：应用层写不出
    越界分（422 挡在门外），只有手改库才可能有；若让均值把它算进去，会出现
    `ratings_last_7d != sum(distribution)` 且均值被拉偏。

    均值保留 1 位小数且**四舍五入**（4.25 -> 4.3，不是 Python ``round`` 的银行家
    舍入 4.2）；无样本 None（不返回 0 冒充）。

    留言：库内原文 -> ``redact_contact`` -> 截断 60 字（**先掩后截**：先截会把
    手机号切断成掩不住的残片）。
    """
    valid = [(score, comment) for score, comment, _created_at in rows if score in RATING_SCORES]
    distribution = {str(score): 0 for score in RATING_SCORES}
    for score, _comment in valid:
        distribution[str(score)] += 1
    total = len(valid)
    average = (
        math.floor(sum(score for score, _c in valid) / total * 10 + 0.5) / 10
        if total
        else None
    )
    comments = [
        redact_contact(comment).strip()[:CSAT_COMMENT_MAX_CHARS]
        for _score, comment in valid
        if comment and comment.strip()
    ][:CSAT_COMMENT_LIMIT]
    return StatsCsat(
        ratings_last_7d=total,
        average_last_7d=average,
        distribution=distribution,
        recent_comments=comments,
    )


# ---------- 端点 ----------


@router.get("/overview", response_model=StatsOverview)
def stats_overview(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> StatsOverview:
    """操作者仪表聚合（控制台读接口，同样要求登录，0016）。"""
    del operator  # 控制台读接口同样要求登录（0016）
    since = window_start(datetime.now(UTC))
    days = window_dates(since)
    by_day = {day: _empty_daily(day) for day in days}

    # 会话：窗口内 created_at 归桶 + 合计（一次拉回，Python 归桶）
    session_days = [
        _utc_day(created_at)
        for created_at in db.scalars(
            select(ServiceSession.created_at).where(ServiceSession.created_at >= since)
        )
    ]
    for day in session_days:
        if day in by_day:
            by_day[day].sessions += 1

    # agent 消息：kind/created_at/citations 一次拉回，拒答/转人工/回答/带引用
    # 回答都在这里算；customer 消息 kind 为 NULL，天然排除
    message_rows = list(
        db.execute(
            select(
                ServiceMessage.kind,
                ServiceMessage.created_at,
                ServiceMessage.citations,
            ).where(ServiceMessage.created_at >= since, ServiceMessage.kind.is_not(None))
        ).all()
    )
    refusals = 0
    handoffs = 0
    answers = 0
    answers_with_citations = 0
    for kind, created_at, citations in message_rows:
        day = _utc_day(created_at)
        if kind == "refusal":
            refusals += 1
            if day in by_day:
                by_day[day].refusals += 1
        elif kind == "handoff":
            handoffs += 1
        elif kind == "answer":
            answers += 1
            # citations 为 JSONB：None / 空列表 / 非列表都算「无引用」
            if isinstance(citations, list) and len(citations) > 0:
                answers_with_citations += 1

    # 被踩：谓词下推 SQL（feedback @> '{"helpful": false}'），NULL/键缺失不命中
    thumbs_down = 0
    for created_at in db.scalars(
        select(ServiceMessage.created_at).where(
            ServiceMessage.created_at >= since,
            ServiceMessage.feedback.contains(_THUMBS_DOWN),
        )
    ):
        thumbs_down += 1
        day = _utc_day(created_at)
        if day in by_day:
            by_day[day].thumbs_down += 1

    # 缺口两个口径分开：新开按 created_at，已解决按 resolved_at（不是当前
    # status 过滤——否则「本周解决数」会被之后重开的缺口抹掉）
    gaps_opened = db.scalar(
        select(func.count())
        .select_from(KnowledgeGap)
        .where(KnowledgeGap.created_at >= since)
    ) or 0
    gaps_resolved = db.scalar(
        select(func.count())
        .select_from(KnowledgeGap)
        .where(KnowledgeGap.resolved_at >= since)
    ) or 0

    # 角标：当前态计数（与列表页同源谓词），不随时间窗收窄
    open_gaps = db.scalar(
        select(func.count())
        .select_from(KnowledgeGap)
        .where(KnowledgeGap.status == "open")
    ) or 0
    pending_qc = db.scalar(
        select(func.count())
        .select_from(MaterialTask)
        .where(MaterialTask.status == "pending_qc")
    ) or 0
    open_tickets = db.scalar(
        select(func.count())
        .select_from(HandoffTicket)
        .where(HandoffTicket.status == "pending")
    ) or 0

    # 反馈汇总：被踩消息的 citations 展开 asset_id 聚合 top 3（复用 customer
    # 路由的防御式解析，不重造）。取全量而非窗口：规格未给窗口限定，且「被踩
    # 最多」是全局信号；citations 逐条 triage_asset_ids 已处理坏行。
    feedback_counts: Counter[int] = Counter()
    for citations in db.scalars(
        select(ServiceMessage.citations).where(
            ServiceMessage.feedback.contains(_THUMBS_DOWN)
        )
    ):
        feedback_counts.update(triage_asset_ids(list(citations or [])))
    top = sorted(feedback_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:FEEDBACK_ASSET_LIMIT]
    top_ids = [asset_id for asset_id, _ in top]
    titles = (
        dict(db.execute(select(Asset.id, Asset.title).where(Asset.id.in_(top_ids))).all())
        if top_ids
        else {}
    )
    feedback_assets = [
        StatsFeedbackAsset(asset_id=asset_id, title=titles.get(asset_id), count=count)
        for asset_id, count in top
    ]

    # CSAT（第 48 刀）：近 7 日评分一趟拉回（score/comment/created_at），分布、
    # 均值、最近留言都在这里算；留言出口必掩（0038）
    csat = _build_csat(
        list(
            db.execute(
                select(SessionRating.score, SessionRating.comment, SessionRating.created_at)
                .where(SessionRating.created_at >= since)
                .order_by(SessionRating.created_at.desc(), SessionRating.id.desc())
            ).all()
        )
    )

    return StatsOverview(
        window_days=WINDOW_DAYS,
        daily=[by_day[day] for day in days],
        sessions_last_7d=len(session_days),
        refusals_last_7d=refusals,
        handoffs_last_7d=handoffs,
        thumbs_down_last_7d=thumbs_down,
        gaps_opened_last_7d=gaps_opened,
        gaps_resolved_last_7d=gaps_resolved,
        answers_last_7d=answers,
        answers_with_citations_last_7d=answers_with_citations,
        # 分子为 0（含分母为 0）时 None：拒答按定义无引用，不给「0% 准确率」误导
        citation_rate_last_7d=(
            answers_with_citations / answers if answers_with_citations else None
        ),
        badges=StatsBadges(
            open_gaps=open_gaps, pending_qc=pending_qc, open_tickets=open_tickets
        ),
        feedback_assets=feedback_assets,
        csat=csat,
    )
