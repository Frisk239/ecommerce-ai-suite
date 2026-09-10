"""审计读接口（0005/0016）：留痕列表，append-only，不进检索。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import AuditLog, Operator

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditOut(BaseModel):
    id: int
    operator_id: int
    # 资产留痕行非空；第 41 刀改价产品档（action='price_change'）这两列为 NULL。
    asset_id: int | None
    version_no: int | None
    action: str  # "publish" | "confirm" | "rollback" | ... | "price_change"
    # 改价留痕指向的商品（资产留痕行为 NULL）；按 asset_id 过滤时天然排除。
    product_id: int | None
    created_at: datetime


@router.get("", response_model=list[AuditOut])
def list_audit(
    assetId: Annotated[int | None, Query()] = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[AuditOut]:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(200)
    if assetId is not None:
        query = query.where(AuditLog.asset_id == assetId)
    return [
        AuditOut(
            id=row.id,
            operator_id=row.operator_id,
            asset_id=row.asset_id,
            version_no=row.version_no,
            action=row.action,
            product_id=row.product_id,
            created_at=row.created_at,
        )
        for row in db.scalars(query)
    ]
