"""血缘视图服务（第 20 刀，ADR 0026）：资产详情上的派生视图，只读拼装。

血缘不是中台对象（0026）：零新表、零新写路径、不进检索、不能发布。权威仍在
商品、资产和这些记录里——本模块只把三环从现有数据拼出来：

- **从哪来**：assets 行（source_kind）。登记时间 assets 表没有存列（本刀零加
  列），契约的 ``origin.created_at`` 如实恒为 null，不发明时间。
- **版本与审计**：audit_log 按资产倒序（publish/confirm/rollback 全列），
  操作者名 join operators。
- **被谁用过**：
  - 引用：service_messages.citations JSONB containment 下推
    （``citations @> '[{"asset_id": N}]'``，禁止全表拉回 Python 过滤），
    样例时间倒序上限 10 + 同条件 count 总计数；问句=该 agent 消息同会话内
    最近一条 customer 消息（相关子查询同语句下推）。
  - 写回：audit_log action=publish 的行——0010 写回随发布同事务，发布事件
    即写回事件。audit_log 没存字段名，如实不带 fields 键；product_id 取
    assets 行（未挂商品为 null）。
  - 考核：coach_records.question_key JSONB containment（题源锚含 asset_id）。

纯拼装函数 ``assemble_lineage`` 与查询语句构造器（``citation_*_stmt`` /
``coaching_stmt``）分开：前者不依赖 DB 可单测（三源聚合/截断/样例上限/空态），
后者的 SQL 形状可编译断言钉死下推（``@>`` 出现在编译 SQL 里）。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, aliased

from suite_api.models import Asset, AuditLog, CoachRecord, Operator, ServiceMessage

# 引用样例上限（spec Must 1：样例 10 + 总计数）；问句/题面截断口径同
# service 会话列表的 first_question 摘要（60 字符 + 省略号）
SAMPLE_LIMIT = 10
_SUMMARY_CHARS = 60


def _summary(text: str) -> str:
    return text[:_SUMMARY_CHARS] + ("…" if len(text) > _SUMMARY_CHARS else "")


def _int_or_none(value: Any) -> int | None:
    # JSONB 回读是 Python 值：bool 是 int 的子类，防御性排除
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


# ---------- 响应契约（给前端票的形状，spec Must 1） ----------


class LineageOriginOut(BaseModel):
    source_kind: str
    # assets 行无登记时间列（零新列）：如实 null，前端不渲染
    created_at: datetime | None


class AuditEventOut(BaseModel):
    at: datetime
    action: str  # publish | confirm | rollback
    version_no: int
    operator: str


class CitationSampleOut(BaseModel):
    session_id: int
    question: str  # 该引用回答对应的顾客问句（截断摘要）
    version_no: int
    at: datetime


class CitationsBlockOut(BaseModel):
    total: int  # 同条件计数（引用本资产的消息条数），与样例上限无关
    samples: list[CitationSampleOut]  # 时间倒序，最多 SAMPLE_LIMIT 条


class WritebackOut(BaseModel):
    at: datetime
    version_no: int
    operator: str
    product_id: int | None
    # fields 键不提供：audit_log 不存写回字段名（如实拼装，不现编语义）


class CoachingUsageOut(BaseModel):
    record_id: int
    question: str  # 题面快照（截断摘要）
    version_no: int
    at: datetime


class LineageUsagesOut(BaseModel):
    citations: CitationsBlockOut
    writebacks: list[WritebackOut]
    coaching: list[CoachingUsageOut]


class AssetLineageOut(BaseModel):
    origin: LineageOriginOut
    versions_audit: list[AuditEventOut]
    usages: LineageUsagesOut


# ---------- 纯拼装（不依赖 DB，单测覆盖） ----------


def _cited_version_no(citations: list[dict[str, Any]] | None, asset_id: int) -> int | None:
    """消息引用列表里本资产的版本号（同消息理论上只出现一版，取最大防御）。"""
    versions: list[int] = []
    for item in citations or []:
        if not isinstance(item, dict) or item.get("asset_id") != asset_id:
            continue
        version = _int_or_none(item.get("version_no"))
        if version is not None:
            versions.append(version)
    return max(versions) if versions else None


def assemble_lineage(
    *,
    asset_id: int,
    source_kind: str,
    product_id: int | None,
    audit_rows: list[tuple[datetime, str, int, str]],
    # (at, action, version_no, operator)——按 audit_log id 倒序
    citation_rows: list[tuple[int, str | None, list[dict[str, Any]] | None, datetime]],
    # (session_id, question, citations, at)——按消息 id 倒序、已 limit
    citations_total: int,
    coach_rows: list[tuple[int, str, dict[str, Any], datetime]],
    # (record_id, question_text, question_key, at)——按记录 id 倒序
) -> AssetLineageOut:
    """三源聚合成血缘载荷：版本号从 JSONB 原样提取（提不出不编造，样例行丢弃
    但计入总数）、问句/题面截断、引用样例硬上限 SAMPLE_LIMIT（查询已 limit，
    这里再守一道，纯函数自含契约）、三块全空即空态（前端显示「还没有被使用
    的记录」）。"""
    versions_audit = [
        AuditEventOut(at=at, action=action, version_no=version_no, operator=operator)
        for at, action, version_no, operator in audit_rows
    ]
    samples: list[CitationSampleOut] = []
    for session_id, question, citations, at in citation_rows:
        version_no = _cited_version_no(citations, asset_id)
        if version_no is None:
            continue  # 计数照常（同条件命中），样例不发明版本
        samples.append(
            CitationSampleOut(
                session_id=session_id,
                question=_summary(question or "—"),
                version_no=version_no,
                at=at,
            )
        )
        if len(samples) >= SAMPLE_LIMIT:
            break
    writebacks = [
        WritebackOut(at=at, version_no=version_no, operator=operator, product_id=product_id)
        for at, action, version_no, operator in audit_rows
        if action == "publish"
    ]
    coaching: list[CoachingUsageOut] = []
    for record_id, question_text, question_key, at in coach_rows:
        version_no = _int_or_none(question_key.get("version_no"))
        if version_no is None:
            continue  # 题源锚缺版本属坏数据：不编造，跳过样例
        coaching.append(
            CoachingUsageOut(
                record_id=record_id,
                question=_summary(question_text),
                version_no=version_no,
                at=at,
            )
        )
    return AssetLineageOut(
        origin=LineageOriginOut(source_kind=source_kind, created_at=None),
        versions_audit=versions_audit,
        usages=LineageUsagesOut(
            citations=CitationsBlockOut(total=citations_total, samples=samples),
            writebacks=writebacks,
            coaching=coaching,
        ),
    )


# ---------- 查询构造（SQL 下推，编译断言可钉形状） ----------


def citation_count_stmt(asset_id: int) -> Select:
    """引用总计数：同一样例条件（JSONB containment 下推）的 count。"""
    return (
        select(func.count())
        .select_from(ServiceMessage)
        .where(ServiceMessage.citations.contains([{"asset_id": asset_id}]))
    )


def citation_sample_stmt(asset_id: int) -> Select:
    """引用样例：命中消息倒序 limit 10，问句用相关子查询取同会话最近一条
    customer 消息——单条 SQL 下推，不在 Python 里二次查。"""
    agent = aliased(ServiceMessage)
    question_sq = (
        select(ServiceMessage.content)
        .where(
            ServiceMessage.session_id == agent.session_id,
            ServiceMessage.role == "customer",
            ServiceMessage.id < agent.id,
        )
        .order_by(ServiceMessage.id.desc())
        .limit(1)
        .correlate(agent)
        .scalar_subquery()
    )
    return (
        select(agent.session_id, question_sq.label("question"), agent.citations, agent.created_at)
        .where(agent.citations.contains([{"asset_id": asset_id}]))
        .order_by(agent.id.desc())
        .limit(SAMPLE_LIMIT)
    )


def coaching_stmt(asset_id: int) -> Select:
    """考核抽题：question_key 题源锚 containment（含 asset_id 即命中，
    其余键不参与匹配）。"""
    return (
        select(
            CoachRecord.id,
            CoachRecord.question_text,
            CoachRecord.question_key,
            CoachRecord.created_at,
        )
        .where(CoachRecord.question_key.contains({"asset_id": asset_id}))
        .order_by(CoachRecord.id.desc())
    )


def audit_rows_stmt(asset_id: int) -> Select:
    """版本与审计：本资产全部留痕倒序，join operators 取操作者名。"""
    return (
        select(AuditLog.created_at, AuditLog.action, AuditLog.version_no, Operator.username)
        .join(Operator, AuditLog.operator_id == Operator.id)
        .where(AuditLog.asset_id == asset_id)
        .order_by(AuditLog.id.desc())
    )


# ---------- 装配入口 ----------


def fetch_asset_lineage(db: Session, asset: Asset) -> AssetLineageOut:
    """一次请求四查询（audit / citations 计数 / citations 样例 / coach），
    全部条件在 SQL 里——无任何全表拉回。"""
    audit_rows = [tuple(row) for row in db.execute(audit_rows_stmt(asset.id)).all()]
    citation_rows = [tuple(row) for row in db.execute(citation_sample_stmt(asset.id)).all()]
    citations_total = db.scalar(citation_count_stmt(asset.id)) or 0
    coach_rows = [tuple(row) for row in db.execute(coaching_stmt(asset.id)).all()]
    return assemble_lineage(
        asset_id=asset.id,
        source_kind=asset.source_kind,
        product_id=asset.product_id,
        audit_rows=audit_rows,  # type: ignore[arg-type]
        citation_rows=citation_rows,  # type: ignore[arg-type]
        citations_total=citations_total,
        coach_rows=coach_rows,  # type: ignore[arg-type]
    )
