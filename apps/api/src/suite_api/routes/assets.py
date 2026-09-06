"""治理台资产路由：登记（0013）、机洗重试（0012 就地）、人洗确认、发布（0005/0010）。

状态机：ingested --机洗成功--> pending_review --发布--> published；
机洗失败停 ingested 存 last_error；已发布/已接入版本不可改（0006）。
发布为单事务：版本 published -> 资产指针前移 -> 商品写回 -> 审计一行
-> 解决登记时关联的知识缺口（0024：解决动作随发布发生）。

登记骨架（put_bytes -> Asset/AssetVersion -> 机洗推进）与资产读视图装配
分别在 services/registration.py 与 services/asset_view.py，供 service 路由共用。
source_kind（0025）与补文档缺口关联（0024）都在本路由按端点语义定值。
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import (
    Asset,
    AssetVersion,
    AuditLog,
    KnowledgeGap,
    Operator,
    Product,
    RetrievalChunk,
)
from suite_api.services.asset_view import (
    AssetDetail,
    AssetOut,
    VersionOut,
    load_products,
    published_version_nos,
    to_asset_detail,
    to_asset_out,
)
from suite_api.services.knowledge_gaps import OPEN, resolve_gaps_for_asset
from suite_api.services.machine_wash import MachineWashError, run_machine_wash
from suite_api.services.publishing import (
    evaluate_publish_gate,
    publishable_values,
    schema_field_names,
)
from suite_api.services.registration import INGESTED, PENDING_REVIEW, register_asset
from suite_api.services.retrieval import ChunkingError, index_chunks_for_version
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/assets", tags=["assets"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"text/plain", "text/markdown"}

PUBLISHED = "published"
_VALID_STATUSES = {INGESTED, PENDING_REVIEW, PUBLISHED}


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
    knowledgeGapId: Annotated[int | None, Form()] = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """登记（0013 没有字节不能登记）：字节先落对象存储，再写库，再同步机洗。

    骨架与回流登记共享 services/registration.register_asset；此处只做上传
    入参校验（类型/大小/空文件）。source_kind 固定 upload（0025：服务端按
    端点语义定值，不让调用方填报）。knowledgeGapId=「补文档」关联（0024，
    原型 fillsGapId 语义）：缺口须存在且 open；登记后 resolved_by_asset_id
    指向本资产（缺口仍 open，发布事务内才置 resolved）。
    """
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

    # 缺口关联先校验（在字节落库前失败）；预填的标题/商品只是前端便利，后端不强制
    gap = None
    if knowledgeGapId is not None:
        gap = db.get(KnowledgeGap, knowledgeGapId)
        if gap is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"知识缺口不存在: {knowledgeGapId}",
            )
        if gap.status != OPEN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"只有待补（open）的知识缺口可以关联，当前状态: {gap.status}",
            )

    try:
        asset = register_asset(
            db,
            storage,
            kind="document",
            title=title,
            content_bytes=data,
            filename=file.filename,
            product_id=productId,
            source_kind="upload",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if gap is not None:
        gap.resolved_by_asset_id = asset.id
    db.commit()
    db.refresh(asset)
    return to_asset_detail(db, asset)


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
    return to_asset_detail(db, asset)


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
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """发布：API 层再校验闸门 -> 单事务（版本/切块入索引/指针/写回/审计）。"""
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
    # 单事务：版本 published -> 切块入索引（发布的一部分，CONTEXT「已发布」词条）
    # -> 资产指针前移 -> 商品写回 -> 审计一行（0005/0006/0010）。
    # 切块失败（字节不可读/非 UTF-8）整体回滚：索引没写就不算发布成功（0004）。
    try:
        index_chunks = index_chunks_for_version(
            storage, version.object_key, asset.kind, confirmed
        )
    except ChunkingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"无法切块入检索索引，发布已中止: {exc}",
        ) from exc
    version.published_at = datetime.now(UTC)
    asset.status = PUBLISHED
    asset.current_published_version_id = version.id
    db.add_all(
        RetrievalChunk(
            asset_id=asset.id, version_no=version.version_no, seq=seq, chunk=chunk
        )
        for seq, chunk in enumerate(index_chunks)
    )
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
    # 0024：发布事务内解决登记时关联的知识缺口（open -> resolved + 指向本资产）。
    # 解决动作随发布发生，无独立手动关闭端点；发布失败整体回滚，缺口保持 open。
    resolve_gaps_for_asset(db, asset.id, resolved_at=version.published_at)
    db.commit()
    db.refresh(asset)
    db.refresh(version)
    return to_asset_detail(db, asset)


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
    products = load_products(db, assets)
    version_nos = published_version_nos(db, assets)
    return [to_asset_out(a, products, version_nos) for a in assets]


@router.get("/{asset_id}", response_model=AssetDetail)
def get_asset(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetDetail:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    asset = _get_asset_or_404(db, asset_id)
    return to_asset_detail(db, asset)
