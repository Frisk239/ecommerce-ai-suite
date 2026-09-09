"""知识缺口共享服务（ADR 0024/0030）：拒答落缺口（归一化幂等）与发布解决缺口。

缺口不是资产：不能检索、不能发布，也没有手动关闭端点——产生只随无证据
拒答（0018 refusal；工具失败转人工不产生缺口——第 13 刀订单工具起该路径
真实存在，kind="handoff" 不走本服务，契约由集成测试钉死），
解决只随发布（补文档=新登记或已发布规格上开修订，发布事务内置 resolved）。

第 30 刀：幂等键从「原问精确匹配」升为「归一化问」（normalize_question，
去首尾空白+全角标点转半角+去尾部 ？！。，、； 等问句尾标点）——同问法差一个
尾标点不再各开一条 open 缺口。归一化只做查重键：question 列仍存顾客原问
（展示与出口掩不受影响），resolved 语义不动（resolved 同文可并存照旧）。
"""

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from suite_api.models import KnowledgeGap

OPEN = "open"
RESOLVED = "resolved"

# 全角标点 → 半角（查重键统一形态；与迁移 0015 的 translate 集保持一致）。
# 、并到 ,（列举逗号）；～ 并到 ~。只映射标点，不动字母数字——归一化是
# 工程查重键，不是语义分析。
_FULLWIDTH_PUNCT = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "！": "!",
        "？": "?",
        "；": ";",
        "：": ":",
        "、": ",",
        "（": "(",
        "）": ")",
        "～": "~",
    }
)
# 问句尾标点（全角在前、半角在后；rstrip 按集合去，循环到非集合字符为止）
_TRAILING_PUNCT = "？?！!。，、；;.,"


def normalize_question(question: str) -> str:
    """缺口查重键（第 30 刀）：strip → 全角标点转半角 → 去尾部问句标点。

    纯函数无 IO；与迁移 0015 的 SQL 回填同口径（translate 集与 rtrim 集合
    一致），存量行回填后与应用层查重可比。只用于幂等键——question 原问列
    与出口视图不经本函数。
    """
    return question.strip().translate(_FULLWIDTH_PUNCT).rstrip(_TRAILING_PUNCT)


def record_refusal_gap(db: Session, question: str) -> KnowledgeGap:
    """拒答落缺口（0024，第 30 刀归一化幂等）：归一化问相同且 open 时复用。

    在拒答消息同一事务内调用（复用与否与 agent 消息一起提交/回滚）；question
    为顾客原问（已 strip，与 customer 消息同文），question 列存原问、
    normalized_question 存归一化值做查重。快路径仍是应用层
    check-then-insert；并发窗口由部分唯一索引
    ``uq_knowledge_gaps_open_normalized``（open 同归一化问）兜底——IntegrityError
    时 SAVEPOINT 回滚插入，再查已有 open 行返回。不可 session.rollback()：
    会把同事务尚未提交的拒答消息一并丢掉。product_id 留空：拒答路径无法从
    自由文本可靠归属商品，不猜。flush 拿 id 供 SSE complete 的 gap_id
    （ADR 0030：运行时返回，不在消息表加列）。
    """
    normalized = normalize_question(question)
    gap = db.scalar(
        select(KnowledgeGap).where(
            KnowledgeGap.normalized_question == normalized, KnowledgeGap.status == OPEN
        )
    )
    if gap is not None:
        return gap
    gap = KnowledgeGap(question=question, normalized_question=normalized, status=OPEN)
    try:
        with db.begin_nested():
            db.add(gap)
            db.flush()
    except IntegrityError:
        # SAVEPOINT 已回滚插入；未 expunge 则后续 SELECT 的 autoflush 会再插一次
        if gap in db:
            db.expunge(gap)
        gap = db.scalar(
            select(KnowledgeGap).where(
                KnowledgeGap.normalized_question == normalized, KnowledgeGap.status == OPEN
            )
        )
        if gap is None:
            raise
    return gap


def load_attachable_gap(db: Session, gap_id: int) -> KnowledgeGap:
    """登记/开修订挂缺口（0024/0031）：须存在且 open，且尚未挂补文档。

    二次挂会覆盖 resolved_by_asset_id，首份发布时 resolve_gaps_for_asset
    按该列查不到缺口，永远 open——故 409。调用方在拿到资产主键后再写指向。
    """
    gap = db.get(KnowledgeGap, gap_id)
    if gap is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"知识缺口不存在: {gap_id}",
        )
    if gap.status != OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有待补（open）的知识缺口可以关联，当前状态: {gap.status}",
        )
    if gap.resolved_by_asset_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"该缺口已有登记中的补文档 A-{gap.resolved_by_asset_id}，请先发布它或换一条缺口"
            ),
        )
    return gap


def resolve_gaps_for_asset(
    db: Session, asset_id: int, *, resolved_at: datetime
) -> list[KnowledgeGap]:
    """发布事务内解决缺口（0024/ADR 0030）：登记时经 knowledge_gap_id 关联到
    本资产（resolved_by_asset_id 已在登记时指向本资产）且仍 open 的缺口，
    置 resolved + resolved_at。解决动作随发布发生——发布失败整体回滚，缺口
    保持 open。多缺口场景：当前登记只关联一个，按一个处理，不过度设计。
    """
    gaps = list(
        db.scalars(
            select(KnowledgeGap).where(
                KnowledgeGap.resolved_by_asset_id == asset_id,
                KnowledgeGap.status == OPEN,
            )
        )
    )
    for gap in gaps:
        gap.status = RESOLVED
        gap.resolved_at = resolved_at
    return gaps
