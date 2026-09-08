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
from suite_api.services.registration import PENDING_REVIEW, PUBLISHED
from suite_platform.storage import ObjectStorage


class VersionTextError(Exception):
    """版本正文读取失败（对象缺失/非 UTF-8）：MCP get_asset/export 用的服务层错误，
    与检索切块的 ChunkingError 分开（消费方不同：一个中断发布事务，一个冒泡成
    工具错误）。"""


def read_version_text(session: Session, storage: ObjectStorage, version: AssetVersion) -> str:
    """从对象存储读回该版本字节并按 UTF-8 解码（ADR 0003：字节只住对象存储）。

    session 与其他服务函数同构（调用方都在会话内），读取本身只依赖对象键。
    decode 失败给明确错误，不静默替换字符（外部 Agent 拿到的正文必须与
    登记字节一致）。
    """
    del session  # 读取只走对象存储；参数保留对齐服务层签名（调用方无需特判）
    try:
        data = storage.get_bytes(version.object_key)
    except FileNotFoundError as exc:
        raise VersionTextError(f"版本对象缺失，无法读取正文: {version.object_key}") from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VersionTextError("版本字节不是合法 UTF-8 文本，无法读取正文") from exc


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
    revising: bool  # 指针已设且存在未发布版本（修订中；线上仍服务指针版）


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


def load_product_names(db: Session, product_ids: set[int]) -> dict[int, str]:
    """批取商品名（debt-2 第 24 刀 N+1 收口）：列表视图一次 IN 查询替代逐行
    db.get(Product)。material/clips 列表同型消费者；单行详情端点不涉。"""
    ids = {pid for pid in product_ids if pid is not None}
    if not ids:
        return {}
    return {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_(ids)))}


def published_version_nos(db: Session, assets: list[Asset]) -> dict[int, int]:
    ids = {a.current_published_version_id for a in assets if a.current_published_version_id is not None}
    if not ids:
        return {}
    return {v.id: v.version_no for v in db.scalars(select(AssetVersion).where(AssetVersion.id.in_(ids)))}


def revising_asset_ids(db: Session, assets: list[Asset]) -> set[int]:
    """指针已设且存在 published_at IS NULL 的版本 = 修订中。"""
    ids = {a.id for a in assets if a.current_published_version_id is not None}
    if not ids:
        return set()
    return set(
        db.scalars(
            select(AssetVersion.asset_id).where(
                AssetVersion.asset_id.in_(ids),
                AssetVersion.published_at.is_(None),
            )
        )
    )


def to_asset_out(
    asset: Asset,
    products: dict[int, Product],
    version_nos: dict[int, int],
    revising_ids: set[int] | None = None,
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
        revising=asset.id in (revising_ids or set()),
    )


def to_asset_detail(db: Session, asset: Asset) -> AssetDetail:
    base = to_asset_out(
        asset,
        load_products(db, [asset]),
        published_version_nos(db, [asset]),
        revising_asset_ids(db, [asset]),
    )
    versions = list(
        db.scalars(
            select(AssetVersion).where(AssetVersion.asset_id == asset.id).order_by(AssetVersion.version_no)
        )
    )
    latest = versions[-1] if versions else None
    product = db.get(Product, asset.product_id) if asset.product_id is not None else None
    # 与发布端点同口径：必填闸门仅种类=文档且挂商品（CONTEXT「必填字段」词条）
    schema: dict[str, Any] = {}
    if asset.kind == "document" and product is not None:
        schema = dict(product.spec_schema)
    extracted = dict(latest.extracted_fields) if latest is not None else {}
    confirmed = dict(latest.confirmed_fields) if latest is not None else {}
    missing, unconfirmed = evaluate_publish_gate(schema, extracted, confirmed)
    can_publish = (
        latest is not None
        and latest.published_at is None
        and asset.status in {PENDING_REVIEW, PUBLISHED}
    )
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
            publishable=can_publish and not missing and not unconfirmed,
            missing=missing,
            unconfirmed=unconfirmed,
        ),
    )
