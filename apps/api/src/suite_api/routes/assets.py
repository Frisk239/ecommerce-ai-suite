"""治理台资产路由：登记（0013）、CSV 批量导入（第 9 刀，上传通道的批量形态）、
机洗重试（0012 就地）、人洗确认、发布（0005/0010）、开修订/回滚（0006）。

状态机：ingested --机洗成功--> pending_review --发布--> published；
机洗失败停 ingested 存 last_error。开修订不改 status、不移指针（线上继续
服务当前已发布版）；人洗闸门看版本行未发布，不要求 status==pending_review。
发布为单事务：版本 published -> 切块入索引 -> 资产指针前移 -> 商品写回 ->
审计一行 -> 解决关联的知识缺口（0024：解决动作随发布发生）。回滚是单独
移指针（audit rollback），不复用 publish body。

登记骨架（put_bytes -> Asset/AssetVersion -> 机洗推进）与资产读视图装配
分别在 services/registration.py 与 services/asset_view.py，供 service 路由共用。
source_kind（0025）与补文档/修订缺口关联（0024/0031）都在本路由按端点语义定值。
"""

import copy
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import (
    Asset,
    AssetVersion,
    AuditLog,
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
    revising_asset_ids,
    to_asset_detail,
    to_asset_out,
)
from suite_api.services.csv_import import CsvImportFormatError, parse_import_csv
from suite_api.services.knowledge_gaps import load_attachable_gap, resolve_gaps_for_asset
from suite_api.services.machine_wash import MachineWashError, run_machine_wash
from suite_api.services.publishing import (
    evaluate_publish_gate,
    publishable_values,
    schema_field_names,
)
from suite_api.services.registration import (
    INGESTED,
    PENDING_REVIEW,
    PUBLISHED,
    make_object_key,
    register_asset,
)
from suite_api.services.retrieval import ChunkingError, index_chunks_for_version
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/assets", tags=["assets"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"text/plain", "text/markdown"}

_VALID_STATUSES = {INGESTED, PENDING_REVIEW, PUBLISHED}


class OpenRevisionIn(BaseModel):
    knowledge_gap_id: int | None = None


class RollbackIn(BaseModel):
    version_no: int


class CsvCreatedRow(BaseModel):
    row: int
    asset_id: int
    title: str


class CsvSkippedRow(BaseModel):
    row: int
    reason: str


class CsvImportReport(BaseModel):
    created: list[CsvCreatedRow]
    skipped: list[CsvSkippedRow]


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


def _unpublished_version(db: Session, asset: Asset) -> AssetVersion | None:
    return db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.published_at.is_(None)
        )
    )


def _inherit_confirmed(confirmed: dict[str, Any]) -> dict[str, Any]:
    """复制确认字段：有值的条目标 inherited，发布闸门视同已确认、不逼重存。"""
    out: dict[str, Any] = {}
    for field, entry in confirmed.items():
        if isinstance(entry, dict):
            copied = dict(entry)
            value = copied.get("value")
            if isinstance(value, str) and value.strip():
                copied["inherited"] = True
            out[field] = copied
        else:
            out[field] = entry
    return out


def _write_back_product(product: Product | None, asset: Asset, version: AssetVersion) -> None:
    """按版本全量写回（ADR 0034）：商品上「本资产写过的字段」与该资产
    当前已发布版一致——新版写回集覆盖，本资产旧版写过而新版没有的字段清除；
    其他资产写回的字段不受影响。发布与回滚共用，指针与商品口径不漂。"""
    if product is None:
        return
    schema = dict(product.spec_schema)
    writable = publishable_values(
        schema, dict(version.extracted_fields), dict(version.confirmed_fields)
    )
    new_values = {
        field: entry
        for field, entry in dict(product.spec_values).items()
        # 只清本资产自己写的旧字段；他资产写回的保持
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("source"), dict)
            and entry["source"].get("asset_id") == asset.id
            and field not in writable
        )
    }
    for field, value in writable.items():
        new_values[field] = {
            "value": value,
            "source": {"asset_id": asset.id, "version": version.version_no},
        }
    product.spec_values = new_values


def _can_publish(asset: Asset, version: AssetVersion) -> bool:
    """待人洗首发，或已发布资产上的未发布修订。"""
    if version.published_at is not None:
        return False
    return asset.status == PENDING_REVIEW or (
        asset.status == PUBLISHED and asset.current_published_version_id is not None
    )


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
    原型 fillsGapId 语义）：缺口须存在且 open，且未挂登记中的补文档——
    同一缺口同一时间只挂一份，否则二次登记静默覆盖指向，首份发布时
    resolve_gaps_for_asset 按 resolved_by_asset_id 查不到该缺口，永不解决；
    登记后 resolved_by_asset_id 指向本资产（缺口仍 open，发布事务内才置 resolved）。
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="空文件不能登记"
        )

    # 缺口关联先校验（在字节落库前失败）；预填的标题/商品只是前端便利，后端不强制
    gap = load_attachable_gap(db, knowledgeGapId) if knowledgeGapId is not None else None

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


@router.post("/import-csv", response_model=CsvImportReport)
def import_csv(
    file: Annotated[UploadFile, File()],
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> CsvImportReport:
    """CSV 批量导入（第 9 刀数据接入）：上传通道的批量形态（0025 不新增枚举）。

    同步 def：FastAPI 丢线程池跑，200 行线性处理不冻事件循环上的伴流 SSE。
    解析规则在 services/csv_import.parse_import_csv（utf-8-sig / 表头须含
    title 与 content / 行数上限 200 / 逐行空值与超长跳过）。每行独立复用
    register_asset（0013 登记必须带字节：content 文本 UTF-8 编码即字节；
    kind=document、不挂商品、source_kind=upload——挂商品在详情页事后处理，
    v1 不进 CSV）。逐行 commit：尽力而为不整批回滚——登记不是发布（0005
    单事务是发布语义），部分成功可重传补救（同 title 重传=新资产，报告可见）。
    空批次（全跳过/空文件）不报错，报告即答案。
    """
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    data = file.file.read()
    # 与单份登记同一把 2MB 防线（评审处置）：CSV 是文本容器，200 行小文本远低于
    # 此；无上限则巨型文件在被 422 行数拒绝前已整读进内存
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="CSV 文件超过 2MB 上限"
        )
    try:
        rows, skipped = parse_import_csv(data)
    except CsvImportFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    created: list[CsvCreatedRow] = []
    failed: list[tuple[int, str]] = []  # 登记中途意外失败的行（罕见：如 DB 中断）
    for row in rows:
        try:
            asset = register_asset(
                db,
                storage,
                kind="document",
                title=row.title,
                content_bytes=row.content.encode("utf-8"),
                filename=file.filename,
                product_id=None,
                source_kind="upload",
            )
            db.commit()  # 逐行提交：单行失败不回滚此前已成功的行
        except Exception as exc:  # noqa: BLE001 - 尽力而为：单行意外失败记原因不弃整批
            db.rollback()
            failed.append((row.row, f"登记失败：{str(exc)[:200] or exc.__class__.__name__}"))
            continue
        created.append(CsvCreatedRow(row=row.row, asset_id=asset.id, title=row.title))

    skipped_rows = [(s.row, s.reason) for s in skipped] + failed
    skipped_rows.sort(key=lambda item: item[0])  # 报告按行号升序，方便对着文件找
    return CsvImportReport(
        created=created,
        skipped=[CsvSkippedRow(row=row_no, reason=reason) for row_no, reason in skipped_rows],
    )


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
    """人洗：确认机洗值/补填弃权字段。闸门看版本未发布；已接入仍拒绝。"""
    asset = _get_asset_or_404(db, asset_id)
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.version_no == version_no
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产版本不存在")
    if asset.status == INGESTED or version.published_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有待人洗（未发布）的版本可以确认字段，已发布/已接入/历史版本不可改",
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
        merged[field] = {"value": value.strip(), "source": "human"}  # 改动丢掉 inherited
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
    version = _latest_version_or_404(db, asset)
    if not _can_publish(asset, version):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"只有待人洗的新资产或已发布资产上的未发布修订可以发布，当前状态: {asset.status}"
            ),
        )
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
        index_chunks = index_chunks_for_version(storage, version.object_key, asset.kind, confirmed)
    except ChunkingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"无法切块入检索索引，发布已中止: {exc}",
        ) from exc
    version.published_at = datetime.now(UTC)
    asset.status = PUBLISHED
    asset.current_published_version_id = version.id
    db.add_all(
        RetrievalChunk(asset_id=asset.id, version_no=version.version_no, seq=seq, chunk=chunk)
        for seq, chunk in enumerate(index_chunks)
    )
    _write_back_product(product, asset, version)
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


@router.post(
    "/{asset_id}/revisions", response_model=AssetDetail, status_code=status.HTTP_201_CREATED
)
def open_revision(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
    body: OpenRevisionIn | None = None,
) -> AssetDetail:
    """开修订（0006）：复制当前已发布字节到新对象键；status/指针不动。

    跳过机洗：抽取与确认字段从当前已发布版继承（确认条目标 inherited）。
    同一资产最多一个未发布版；二次开修订 409。可选 knowledge_gap_id 语义同登记。
    """
    del operator
    asset = _get_asset_or_404(db, asset_id)
    if asset.current_published_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有已发布资产可以开修订（当前没有已发布版本指针）",
        )
    if _unpublished_version(db, asset) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="同一资产同时最多一个未发布修订",
        )
    gap_id = body.knowledge_gap_id if body is not None else None
    gap = load_attachable_gap(db, gap_id) if gap_id is not None else None

    published = db.get(AssetVersion, asset.current_published_version_id)
    if published is None:  # pragma: no cover - 指针完整性由发布事务保证
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前已发布版本缺失，无法开修订",
        )
    try:
        content_bytes = storage.get_bytes(published.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前已发布版本对象缺失，无法开修订",
        ) from exc
    object_key = make_object_key(asset.kind, content_bytes)
    storage.put_bytes(object_key, content_bytes)

    max_no = db.scalar(
        select(func.max(AssetVersion.version_no)).where(AssetVersion.asset_id == asset.id)
    )
    version = AssetVersion(
        asset_id=asset.id,
        version_no=(max_no or 0) + 1,
        object_key=object_key,
        extracted_fields=copy.deepcopy(dict(published.extracted_fields)),
        confirmed_fields=_inherit_confirmed(copy.deepcopy(dict(published.confirmed_fields))),
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="同一资产同时最多一个未发布修订",
        ) from exc
    if gap is not None:
        gap.resolved_by_asset_id = asset.id
    db.commit()
    db.refresh(asset)
    return to_asset_detail(db, asset)


@router.post("/{asset_id}/rollback", response_model=AssetDetail)
def rollback(
    asset_id: int,
    body: RollbackIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetDetail:
    """回滚：单独移指针到曾经发布过的版本（UX-NOTES：不复用 publish body）。

    目标必须有 published_at；已是当前指针 / 未知 / 未发布 / 有进行中修订 → 409。
    写回该版确认字段；audit rollback；不删旧切块。
    """
    asset = _get_asset_or_404(db, asset_id)
    if _unpublished_version(db, asset) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="有未发布修订时不能回滚，请先发布该修订",
        )
    target = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.version_no == body.version_no
        )
    )
    if target is None or target.published_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只能回滚到曾经发布过的版本",
        )
    if target.id == asset.current_published_version_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"v{body.version_no} 已是当前已发布版本",
        )
    asset.current_published_version_id = target.id
    asset.status = PUBLISHED
    _write_back_product(_product_or_none(db, asset), asset, target)
    db.add(
        AuditLog(
            operator_id=operator.id,
            asset_id=asset.id,
            version_no=target.version_no,
            action="rollback",
        )
    )
    db.commit()
    db.refresh(asset)
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
    if status_filter == PUBLISHED:
        # 已发布口径=指针非空（含修订中：线上仍在服务）
        query = query.where(Asset.current_published_version_id.is_not(None))
    elif status_filter is not None:
        query = query.where(Asset.status == status_filter)
    assets = list(db.scalars(query))
    products = load_products(db, assets)
    version_nos = published_version_nos(db, assets)
    revising_ids = revising_asset_ids(db, assets)
    return [to_asset_out(a, products, version_nos, revising_ids) for a in assets]


@router.get("/{asset_id}", response_model=AssetDetail)
def get_asset(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetDetail:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    asset = _get_asset_or_404(db, asset_id)
    return to_asset_detail(db, asset)
