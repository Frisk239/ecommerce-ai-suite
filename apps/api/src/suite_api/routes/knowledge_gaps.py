"""知识缺口路由（ADR 0024/0030）：治理台缺口 tab 的读列表。

缺口不是资产：这里只有列表读接口——产生只随拒答（service 路由同事务落库），
解决只随发布（assets 路由发布事务内置 resolved），补文档走普通登记或
已发布规格上的开修订（assets.register / revisions 带可选 knowledge_gap_id）。
无创建/手动关闭端点（0024/0031：有已发布规格则默认开修订）。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import KnowledgeGap, Operator, Product
from suite_api.services.knowledge_gaps import OPEN, RESOLVED
from suite_api.services.machine_wash import redact

router = APIRouter(prefix="/api/knowledge-gaps", tags=["knowledge-gaps"])

_VALID_STATUSES = {OPEN, RESOLVED}


class GapProductRef(BaseModel):
    id: int
    name: str


class KnowledgeGapOut(BaseModel):
    id: int
    question: str
    product: GapProductRef | None
    status: str
    resolved_by_asset_id: int | None
    created_at: datetime
    resolved_at: datetime | None
    # 第 39 刀热度：被问次数（归一化幂等命中既有 open 缺口时 +1）
    hit_count: int


@router.get("", response_model=list[KnowledgeGapOut])
def list_knowledge_gaps(
    status_filter: Annotated[str, Query(alias="status")] = OPEN,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[KnowledgeGapOut]:
    """缺口列表（默认 open 待办；倒序）。缺口不是资产，无检索/发布路径。

    第 26 刀（缺口路 P1，0038 修订口径）：question 为顾客原问（拒答路径
    原文落库，落库不动——同 service_messages 豁免），治理台列表是它面向
    操作者的出口视图：返回前过 redact（出口必掩；血缘引用样例同先例）。"""
    del operator  # 控制台读接口同样要求登录（0016）
    if status_filter not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status 只能是 {'/'.join(sorted(_VALID_STATUSES))}",
        )
    gaps = list(
        db.scalars(
            select(KnowledgeGap)
            .where(KnowledgeGap.status == status_filter)
            # 第 39 刀：按热度排（被问次数多=最需要补的排前），同热度新者先
            .order_by(KnowledgeGap.hit_count.desc(), KnowledgeGap.created_at.desc(), KnowledgeGap.id.desc())
        )
    )
    product_ids = {g.product_id for g in gaps if g.product_id is not None}
    products = (
        {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))}
        if product_ids
        else {}
    )
    return [
        KnowledgeGapOut(
            id=g.id,
            # 出口掩（0038 修订）：落库原文不动，视图呈掩码
            question=redact(g.question),
            product=(
                GapProductRef(id=products[g.product_id].id, name=products[g.product_id].name)
                if g.product_id in products
                else None
            ),
            status=g.status,
            resolved_by_asset_id=g.resolved_by_asset_id,
            created_at=g.created_at,
            resolved_at=g.resolved_at,
            hit_count=g.hit_count,
        )
        for g in gaps
    ]
