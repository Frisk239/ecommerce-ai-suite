"""资产读视图组装：响应模型（给前端票的契约）+ AssetDetail 装配。

从 routes/assets.py 下沉到 services 层公开导出：service 路由的回流登记返回
同一 AssetDetail，原先跨路由 import 私有 _to_asset_detail，属私有导入越界。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, Product
from suite_api.services.publishing import evaluate_publish_gate
from suite_api.services.registration import PENDING_REVIEW


class ProductRef(BaseModel):
    id: int
    name: str
    category: str


class AssetOut(BaseModel):
    id: int
    title: str | None
    kind: str
    status: str
    source_kind: str  # 0025 来源（血缘第一环）：登记端点语义定值
    product: ProductRef | None
    last_error: str | None
    current_published_version_no: int | None


class VersionOut(BaseModel):
    version_no: int
    object_key: str
    extracted_fields: dict[str, Any]
    confirmed_fields: dict[str, Any]
    published_at: datetime | None


class Publishability(BaseModel):
    publishable: bool
    missing: list[str]
    unconfirmed: list[str]


class AssetDetail(AssetOut):
    versions: list[VersionOut]
    publishability: Publishability


def load_products(db: Session, assets: list[Asset]) -> dict[int, Product]:
    ids = {a.product_id for a in assets if a.product_id is not None}
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}


def published_version_nos(db: Session, assets: list[Asset]) -> dict[int, int]:
    ids = {a.current_published_version_id for a in assets if a.current_published_version_id is not None}
    if not ids:
        return {}
    return {v.id: v.version_no for v in db.scalars(select(AssetVersion).where(AssetVersion.id.in_(ids)))}


def to_asset_out(
    asset: Asset, products: dict[int, Product], version_nos: dict[int, int]
) -> AssetOut:
    product = products.get(asset.product_id) if asset.product_id is not None else None
    return AssetOut(
        id=asset.id,
        title=asset.title,
        kind=asset.kind,
        status=asset.status,
        source_kind=asset.source_kind,
        product=(
            ProductRef(id=product.id, name=product.name, category=product.category)
            if product
            else None
        ),
        last_error=asset.last_error,
        current_published_version_no=(
            version_nos.get(asset.current_published_version_id)
            if asset.current_published_version_id is not None
            else None
        ),
    )


def to_asset_detail(db: Session, asset: Asset) -> AssetDetail:
    base = to_asset_out(asset, load_products(db, [asset]), published_version_nos(db, [asset]))
    versions = list(
        db.scalars(
            select(AssetVersion).where(AssetVersion.asset_id == asset.id).order_by(AssetVersion.version_no)
        )
    )
    latest = versions[-1] if versions else None
    product = db.get(Product, asset.product_id) if asset.product_id is not None else None
    schema = dict(product.spec_schema) if product is not None else {}
    extracted = dict(latest.extracted_fields) if latest is not None else {}
    confirmed = dict(latest.confirmed_fields) if latest is not None else {}
    missing, unconfirmed = evaluate_publish_gate(schema, extracted, confirmed)
    return AssetDetail(
        **base.model_dump(),
        versions=[
            VersionOut(
                version_no=v.version_no,
                object_key=v.object_key,
                extracted_fields=dict(v.extracted_fields),
                confirmed_fields=dict(v.confirmed_fields),
                published_at=v.published_at,
            )
            for v in versions
        ],
        publishability=Publishability(
            publishable=asset.status == PENDING_REVIEW and not missing and not unconfirmed,
            missing=missing,
            unconfirmed=unconfirmed,
        ),
    )
