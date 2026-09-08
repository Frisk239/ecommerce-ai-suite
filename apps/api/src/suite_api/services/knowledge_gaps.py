"""知识缺口共享服务（ADR 0024/0030）：拒答落缺口（精确幂等）与发布解决缺口。

缺口不是资产：不能检索、不能发布，也没有手动关闭端点——产生只随无证据
拒答（0018 refusal；工具失败转人工不产生，本刀无工具，契约由集成测试钉死），
解决只随发布（「补文档」= 带知识缺口的普通登记，发布事务内置 resolved）。
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import KnowledgeGap

OPEN = "open"
RESOLVED = "resolved"


def record_refusal_gap(db: Session, question: str) -> KnowledgeGap:
    """拒答落缺口（0024）：精确幂等——同 question 文本且 open 时复用，不新建。

    在拒答消息同一事务内调用（复用与否与 agent 消息一起提交/回滚）；question
    为顾客原问（已 strip，与 customer 消息同文），缺 D 级约束的并发窗口由
    单操作者场景消化（0024 未锁去重策略，工程裁决只做应用层精确幂等）。
    product_id 留空：拒答路径无法从自由文本可靠归属商品，不猜。flush 拿 id
    供 SSE complete 事件的 gap_id（ADR 0030：运行时返回，不在消息表加列）。
    """
    gap = db.scalar(
        select(KnowledgeGap).where(
            KnowledgeGap.question == question, KnowledgeGap.status == OPEN
        )
    )
    if gap is not None:
        return gap
    gap = KnowledgeGap(question=question, status=OPEN)
    db.add(gap)
    db.flush()
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
