"""知识缺口路由（ADR 0024/0030）：治理台缺口 tab 的读列表。

缺口不是资产：这里只有列表读接口——产生只随拒答（service 路由同事务落库），
解决只随发布（assets 路由发布事务内置 resolved），补文档走普通登记
（assets.register 带可选 knowledgeGapId）。无创建/手动关闭端点（0024：操作者
从缺口补文档或开修订，修订流属后续刀）。
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


@router.get("", response_model=list[KnowledgeGapOut])
def list_knowledge_gaps(
    status_filter: Annotated[str, Query(alias="status")] = OPEN,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[KnowledgeGapOut]:
    """缺口列表（默认 open 待办；倒序）。缺口不是资产，无检索/发布路径。"""
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
            .order_by(KnowledgeGap.id.desc())
        )
    )
    product_ids = {g.product_id for g in gaps if g.product_id is not None}
    products = (
        {
            p.id: p
            for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))
        }
        if product_ids
        else {}
    )
    return [
        KnowledgeGapOut(
            id=g.id,
            question=g.question,
            product=(
                GapProductRef(id=products[g.product_id].id, name=products[g.product_id].name)
                if g.product_id in products
                else None
            ),
            status=g.status,
            resolved_by_asset_id=g.resolved_by_asset_id,
            created_at=g.created_at,
            resolved_at=g.resolved_at,
        )
        for g in gaps
    ]
