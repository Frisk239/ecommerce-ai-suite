"""商品读写接口：spec_schema（0019 类目模板）与 spec_values（0010 写回值+来源）。

读（GET）：登录操作者可见，stock 只读泄漏（第 30 刀）。

写（第 41 刀，ADR 0045）：POST 上新 / PATCH 改价改档——商品列直写即时
生效（回落报价读实时行价）；机洗/发布写回不碰价格（写回函数只动
spec_values）。改价（price_cents/currency 变化）记 audit 产品档
（action='price_change'，非资产 publish）；非改价 PATCH 不留痕。
"""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import AuditLog, Operator, Product
from suite_api.services.category_schema import schema_for_category

router = APIRouter(prefix="/api/products", tags=["products"])

# 币种：3 字母（ISO 4217 形状校验，v1 单币种不结算，只存不换算）。
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")

# 改价留痕动作（audit_log 产品档：product_id 非空、asset 侧两列 NULL）。
PRICE_CHANGE_ACTION = "price_change"


class ProductOut(BaseModel):
    id: int
    name: str
    category: str
    spec_schema: dict[str, Any]
    spec_values: dict[str, Any]  # {字段: {value, source: {asset_id, version}}}
    # 第 30 刀裁决：stock 只读泄漏到操作者面（治理台商品页自查「库存可 mock」
    # 词条）；ProductOut 仅本路由消费（登录操作者），不进 MCP/顾客面。
    stock: int | None
    # 第 41 刀单价（分，NULL=未定价）与币种（缺省 CNY，v1 单币种不结算）。
    price_cents: int | None
    currency: str


class ProductCreate(BaseModel):
    name: str
    category: str
    price_cents: int | bool | None = None
    currency: str = "CNY"
    # 省略即按类目模板派生；显式给时键集合须与模板一致（按模板校验）。
    spec_schema: dict[str, Any] | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    price_cents: int | bool | None = None
    currency: str | None = None
    spec_schema: dict[str, Any] | None = None


def _product_out(product: Product) -> ProductOut:
    return ProductOut(
        id=product.id,
        name=product.name,
        category=product.category,
        spec_schema=dict(product.spec_schema),
        spec_values=dict(product.spec_values),
        stock=product.stock,
        price_cents=product.price_cents,
        currency=product.currency,
    )


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="商品名须为非空字符串",
        )
    if len(name.strip()) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="商品名超过 200 字上限",
        )
    return name.strip()


def _check_category(category: Any) -> str:
    if not isinstance(category, str) or not category.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="类目须为非空字符串",
        )
    if len(category.strip()) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="类目超过 50 字上限",
        )
    return category.strip()


def _check_price(price_cents: Any) -> int | None:
    """单价校验：NULL=未定价；整数 ≥0（bool 不是价，pydantic 宽进时显式挡）。"""
    if price_cents is None:
        return None
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="单价须为大于等于 0 的整数（分），未定价传 null",
        )
    if price_cents < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="单价不能为负数",
        )
    return price_cents


def _check_currency(currency: Any) -> str:
    if not isinstance(currency, str) or _CURRENCY_RE.fullmatch(currency.strip()) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="币种须为 3 字母（如 CNY），v1 单币种不结算",
        )
    return currency.strip().upper()


def _check_spec_schema(spec_schema: Any, category: str) -> dict[str, Any]:
    """spec_schema 按类目模板校验（0019）：键集合须与模板一致（未知类目模板
    为空，即只接受省略或空对象——不发明字段）。值形状 {required: bool}。」
    """
    template = schema_for_category(category)
    if spec_schema is None:
        return template
    if not isinstance(spec_schema, dict) or set(spec_schema) != set(template):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"规格模板须与类目「{category}」一致：{sorted(template)}",
        )
    for field, rule in spec_schema.items():
        if (
            not isinstance(rule, dict)
            or set(rule) != {"required"}
            or not isinstance(rule["required"], bool)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f'规格字段「{field}」形状非法（应为 {{"required": bool}}）',
            )
    return {field: {"required": bool(rule["required"])} for field, rule in spec_schema.items()}


def _reject_duplicate_name(db: Session, name: str, *, exclude_id: int | None = None) -> None:
    query = select(Product).where(Product.name == name)
    if exclude_id is not None:
        query = query.where(Product.id != exclude_id)
    if db.scalar(query) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"商品名已存在: {name}（改价请走 PATCH，勿重名上新）",
        )


@router.get("", response_model=list[ProductOut])
def list_products(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[ProductOut]:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    return [_product_out(p) for p in db.scalars(select(Product).order_by(Product.id))]


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
    return _product_out(product)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductCreate,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ProductOut:
    """上新：登录操作者在控制台建商品（第 41 刀去补链路的上新端）。

    spec_schema 省略即按类目模板派生；未知类目模板为空（不发明字段）。
    401 未登录 / 422 校验 / 409 重名（改价走 PATCH）。
    """
    del operator
    name = _check_name(body.name)
    category = _check_category(body.category)
    _reject_duplicate_name(db, name)
    product = Product(
        name=name,
        category=category,
        spec_schema=_check_spec_schema(body.spec_schema, category),
        spec_values={},
        price_cents=_check_price(body.price_cents),
        currency=_check_currency(body.currency),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return _product_out(product)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    body: ProductUpdate,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ProductOut:
    """改档/改价：登录操作者直写商品列，即时生效（回落读实时行价）。

    类目变化且未显式带 spec_schema 时，规格模板随新类目重置（旧 spec_values
    按键保留：写回按新模板键取值，模板外旧值自然失效，不删数据）。
    仅当 price_cents/currency 真变才记 audit 产品档（非资产 publish）。
    401 未登录 / 404 无商品 / 422 校验 / 409 重名。
    """
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    if body.name is not None:
        name = _check_name(body.name)
        _reject_duplicate_name(db, name, exclude_id=product.id)
        product.name = name
    effective_category = product.category
    if body.category is not None:
        effective_category = _check_category(body.category)
    if body.spec_schema is not None:
        product.spec_schema = _check_spec_schema(body.spec_schema, effective_category)
    elif body.category is not None and effective_category != product.category:
        product.spec_schema = _check_spec_schema(None, effective_category)
    product.category = effective_category
    price_changed = False
    if body.price_cents is not None or "price_cents" in body.model_fields_set:
        # 显式传 null=改回未定价（与省略不传区分：fields_set 口径）。
        new_price = _check_price(body.price_cents)
        if new_price != product.price_cents:
            product.price_cents = new_price
            price_changed = True
    if body.currency is not None:
        new_currency = _check_currency(body.currency)
        if new_currency != product.currency:
            product.currency = new_currency
            price_changed = True
    if price_changed:
        db.add(
            AuditLog(
                operator_id=operator.id,
                asset_id=None,
                version_no=None,
                action=PRICE_CHANGE_ACTION,
                product_id=product.id,
            )
        )
    db.commit()
    db.refresh(product)
    return _product_out(product)
