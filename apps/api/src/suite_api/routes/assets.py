"""治理台资产路由：登记（0013）、机洗重试（0012 就地）、人洗确认、发布（0005/0010）。

状态机：ingested --机洗成功--> pending_review --发布--> published；
机洗失败停 ingested 存 last_error；已发布/已接入版本不可改（0006）。
发布为单事务：版本 published -> 资产指针前移 -> 商品写回 -> 审计一行。
"""

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import Asset, AssetVersion, AuditLog, Operator, Product
from suite_api.services.machine_wash import MachineWashError, run_machine_wash
from suite_api.services.publishing import (
    evaluate_publish_gate,
    publishable_values,
    schema_field_names,
)
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/assets", tags=["assets"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"text/plain", "text/markdown"}

INGESTED = "ingested"
PENDING_REVIEW = "pending_review"
PUBLISHED = "published"
_VALID_STATUSES = {INGESTED, PENDING_REVIEW, PUBLISHED}


# ---------- 响应模型（给前端票的契约） ----------


class ProductRef(BaseModel):
    id: int
    name: str
    category: str


class AssetOut(BaseModel):
    id: int
    title: str | None
    kind: str
    status: str
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


# ---------- 查询辅助 ----------


def _load_products(db: Session, assets: list[Asset]) -> dict[int, Product]:
    ids = {a.product_id for a in assets if a.product_id is not None}
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}


def _published_version_nos(db: Session, assets: list[Asset]) -> dict[int, int]:
    ids = {a.current_published_version_id for a in assets if a.current_published_version_id is not None}
    if not ids:
        return {}
    return {v.id: v.version_no for v in db.scalars(select(AssetVersion).where(AssetVersion.id.in_(ids)))}


def _to_asset_out(
    asset: Asset, products: dict[int, Product], version_nos: dict[int, int]
) -> AssetOut:
    product = products.get(asset.product_id) if asset.product_id is not None else None
    return AssetOut(
        id=asset.id,
        title=asset.title,
        kind=asset.kind,
        status=asset.status,
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


def _to_asset_detail(db: Session, asset: Asset) -> AssetDetail:
    base = _to_asset_out(asset, _load_products(db, [asset]), _published_version_nos(db, [asset]))
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


def _get_asset_or_404(db: Session, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产不存在")
    return asset


def _latest_version_or_404(db: Session, asset: Asset) -> AssetVersion:
    version = db.scalar(
        select(AssetVersion)
        .where(AssetVersion.asset_id == asset.id)
        .order_by(AssetVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产没有版本")
    return version


def _product_or_none(db: Session, asset: Asset) -> Product | None:
    if asset.product_id is None:
        return None
    product = db.get(Product, asset.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产所挂商品不存在")
    return product


# ---------- 写接口（全部要求登录，401 未登录） ----------


@router.post("/register", response_model=AssetDetail, status_code=status.HTTP_201_CREATED)
async def register(
    file: Annotated[UploadFile, File()],
    productId: Annotated[int | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """登记（0013 没有字节不能登记）：字节先落对象存储，再写库，再同步机洗。"""
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"仅接受 {' / '.join(sorted(ALLOWED_CONTENT_TYPES))}，收到: {file.content_type}",
        )
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文档超过 2MB 上限"
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="空文件不能登记")

    product = None
    if productId is not None:
        product = db.get(Product, productId)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    # 对象键：documents/{uuid}/{sha256前16}.txt；防逃逸由 LocalDirectoryStorage 保证
    digest = hashlib.sha256(data).hexdigest()[:16]
    object_key = f"documents/{uuid4().hex}/{digest}.txt"
    storage.put_bytes(object_key, data)

    asset = Asset(
        kind="document",
        status=INGESTED,
        title=title,
        product_id=product.id if product is not None else None,
    )
    db.add(asset)
    db.flush()  # 拿主键，机洗失败也能以 ingested + last_error 落库
    version = AssetVersion(
        asset_id=asset.id,
        version_no=1,
        object_key=object_key,
        extracted_fields={},
        confirmed_fields={},
    )
    db.add(version)

    field_names = schema_field_names(product.spec_schema) if product is not None else []
    try:
        extracted = run_machine_wash(storage, object_key, field_names)
        version.extracted_fields = extracted  # JSONB 整体赋值，确保变更可追踪
        asset.status = PENDING_REVIEW
    except (MachineWashError, FileNotFoundError) as exc:
        asset.status = INGESTED
        asset.last_error = str(exc)[:500] or exc.__class__.__name__
    db.commit()
    db.refresh(asset)
    db.refresh(version)
    return _to_asset_detail(db, asset)


@router.post("/{asset_id}/retry-machine-wash", response_model=AssetDetail)
def retry_machine_wash(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """机洗失败就地重试（0012 任务不是中台对象，无任务表）：仅已接入态可重试。"""
    del operator
    asset = _get_asset_or_404(db, asset_id)
    if asset.status != INGESTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有已接入（机洗失败）的资产可以重试，当前状态: {asset.status}",
        )
    version = _latest_version_or_404(db, asset)
    product = _product_or_none(db, asset)
    field_names = schema_field_names(product.spec_schema) if product is not None else []
    try:
        extracted = run_machine_wash(storage, version.object_key, field_names)
        version.extracted_fields = extracted
        asset.status = PENDING_REVIEW
        asset.last_error = None
    except (MachineWashError, FileNotFoundError) as exc:
        asset.last_error = str(exc)[:500] or exc.__class__.__name__
    db.commit()
    db.refresh(asset)
    db.refresh(version)
    return _to_asset_detail(db, asset)


@router.patch("/{asset_id}/versions/{version_no}/fields", response_model=VersionOut)
def confirm_fields(
    asset_id: int,
    version_no: int,
    body: dict[str, str],
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> VersionOut:
    """人洗：确认机洗值/补填弃权字段。只有待人洗的版本可改（0006 不可变）。"""
    asset = _get_asset_or_404(db, asset_id)
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.version_no == version_no
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产版本不存在")
    if asset.status != PENDING_REVIEW or version.published_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有待人洗（未发布）的版本可以确认字段，已发布/已接入版本不可改",
        )
    product = _product_or_none(db, asset)
    schema = dict(product.spec_schema) if product is not None else {}
    allowed = schema_field_names(schema)
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"字段不在所挂商品的规格字段集合内: {unknown}",
        )
    blank = sorted(f for f, v in body.items() if not v.strip())
    if blank:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"禁止空字符串冒充已抽取（0009）: {blank}",
        )
    merged = dict(version.confirmed_fields)
    for field, value in body.items():
        merged[field] = {"value": value.strip(), "source": "human"}
    version.confirmed_fields = merged
    # 0016：确认必填字段也留痕（谁/何时/哪版）
    db.add(
        AuditLog(
            operator_id=operator.id,
            asset_id=asset.id,
            version_no=version.version_no,
            action="confirm",
        )
    )
    db.commit()
    db.refresh(version)
    return VersionOut(
        version_no=version.version_no,
        object_key=version.object_key,
        extracted_fields=dict(version.extracted_fields),
        confirmed_fields=dict(version.confirmed_fields),
        published_at=version.published_at,
    )


@router.post("/{asset_id}/publish", response_model=AssetDetail)
def publish(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetDetail:
    """发布：API 层再校验闸门 -> 单事务（版本/指针/写回/审计）。"""
    asset = _get_asset_or_404(db, asset_id)
    if asset.status != PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有待人洗的资产可以发布，当前状态: {asset.status}",
        )
    version = _latest_version_or_404(db, asset)
    product = _product_or_none(db, asset)
    schema = dict(product.spec_schema) if product is not None else {}
    extracted = dict(version.extracted_fields)
    confirmed = dict(version.confirmed_fields)
    missing, unconfirmed = evaluate_publish_gate(schema, extracted, confirmed)
    if missing or unconfirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "publish_gate_failed",
                "missing": missing,  # 缺少必填字段（弃权/未填）
                "unconfirmed": unconfirmed,  # 待确认字段（机洗有值未确认）
            },
        )
    # 单事务：版本 published -> 资产指针前移 -> 商品写回 -> 审计一行（0005/0006/0010）
    version.published_at = datetime.now(UTC)
    asset.status = PUBLISHED
    asset.current_published_version_id = version.id
    if product is not None:
        new_values = dict(product.spec_values)
        for field, value in publishable_values(schema, extracted, confirmed).items():
            new_values[field] = {
                "value": value,
                "source": {"asset_id": asset.id, "version": version.version_no},
            }
        product.spec_values = new_values
    db.add(
        AuditLog(
            operator_id=operator.id,
            asset_id=asset.id,
            version_no=version.version_no,
            action="publish",
        )
    )
    db.commit()
    db.refresh(asset)
    db.refresh(version)
    return _to_asset_detail(db, asset)


# ---------- 读接口 ----------


@router.get("", response_model=list[AssetOut])
def list_assets(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[AssetOut]:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    if status_filter is not None and status_filter not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status 只能是 {'/'.join(sorted(_VALID_STATUSES))}",
        )
    query = select(Asset).order_by(Asset.id.desc())
    if status_filter is not None:
        query = query.where(Asset.status == status_filter)
    assets = list(db.scalars(query))
    products = _load_products(db, assets)
    version_nos = _published_version_nos(db, assets)
    return [_to_asset_out(a, products, version_nos) for a in assets]


@router.get("/{asset_id}", response_model=AssetDetail)
def get_asset(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetDetail:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    asset = _get_asset_or_404(db, asset_id)
    return _to_asset_detail(db, asset)
