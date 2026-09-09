"""商品读接口：spec_schema（0019 类目模板）与 spec_values（0010 写回值+来源）。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import Operator, Product

router = APIRouter(prefix="/api/products", tags=["products"])


class ProductOut(BaseModel):
    id: int
    name: str
    category: str
    spec_schema: dict[str, Any]
    spec_values: dict[str, Any]  # {字段: {value, source: {asset_id, version}}}
    # 第 30 刀裁决：stock 只读泄漏到操作者面（治理台商品页自查「库存可 mock」
    # 词条）；ProductOut 仅本路由消费（登录操作者），不进 MCP/顾客面。
    stock: int | None


@router.get("", response_model=list[ProductOut])
def list_products(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[ProductOut]:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    return [
        ProductOut(
            id=p.id,
            name=p.name,
            category=p.category,
            spec_schema=dict(p.spec_schema),
            spec_values=dict(p.spec_values),
            stock=p.stock,
        )
        for p in db.scalars(select(Product).order_by(Product.id))
    ]


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ProductOut:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return ProductOut(
        id=product.id,
        name=product.name,
        category=product.category,
        spec_schema=dict(product.spec_schema),
        spec_values=dict(product.spec_values),
        stock=product.stock,
    )
