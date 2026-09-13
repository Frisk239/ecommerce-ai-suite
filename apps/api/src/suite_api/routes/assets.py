"""治理台资产路由：登记（0013）、CSV 批量导入（第 9 刀，上传通道的批量形态）、
机洗重试（0012 就地）、人洗确认、发布（0005/0010）、开修订/回滚（0006）、
生命周期出口三件（0042：修订换字节/放弃修订/废弃失败资产）。

状态机：ingested --机洗成功--> pending_review --发布--> published；
机洗失败停 ingested 存 last_error。开修订不改 status、不移指针（线上继续
服务当前已发布版）；人洗闸门看版本行未发布，不要求 status==pending_review。
发布为单事务：版本 published -> 切块入索引 -> 资产指针前移 -> 商品写回 ->
审计一行 -> 解决关联的知识缺口（0024：解决动作随发布发生）。回滚是单独
移指针（audit rollback），不复用 publish body。出口三件（0042）都是负向动作：
换字节只碰未发布版（线上字节永不动）、放弃修订删未发布版（字节+行）、
废弃只限「已接入且从未发布」（discarded_at 标记隐藏，不是第四态）。

登记骨架（put_bytes -> Asset/AssetVersion -> 机洗推进）与资产读视图装配
分别在 services/registration.py 与 services/asset_view.py，供 service 路由共用。
source_kind（0025）与补文档/修订缺口关联（0024/0031）都在本路由按端点语义定值。
"""

import copy
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
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
    VersionTextError,
    load_products,
    published_version_nos,
    read_version_text,
    revising_asset_ids,
    to_asset_detail,
    to_asset_out,
)
from suite_api.services.csv_import import CsvImportFormatError, parse_import_csv
from suite_api.services.knowledge_gaps import load_attachable_gap, resolve_gaps_for_asset
from suite_api.services.lineage import AssetLineageOut, fetch_asset_lineage
from suite_api.services.machine_wash import (
    QA_FIELD,
    MachineWashError,
    redact,
    run_machine_wash,
    validate_qa_pairs,
)
from suite_api.services.publishing import (
    evaluate_publish_gate,
    publishable_values,
)
from suite_api.services.registration import (
    INGESTED,
    PENDING_REVIEW,
    PUBLISHED,
    SOURCE_KINDS,
    machine_wash_field_names,
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
    """复制确认字段：有值的条目标 inherited，发布闸门视同已确认、不逼重存。

    值两类形状（ADR 0035）：str（文档字段）非空白才算有值；list（dialogue 的
    qa_pairs 数组）非空数组才算有值——第 16 刀 P2 修正：数组值同样打 inherited
    标签，前端 QA 面板据此显示「继承自已发布版」（此前空标显示「已确认」，
    口径与 str 值不一致）。
    """
    out: dict[str, Any] = {}
    for field, entry in confirmed.items():
        if isinstance(entry, dict):
            copied = dict(entry)
            value = copied.get("value")
            if isinstance(value, str) and value.strip():
                copied["inherited"] = True
            elif isinstance(value, list) and value:
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
    """待人洗首发，或已发布资产上的未发布修订。

    第 84 刀（83 刀评审记债）：已废弃资产（0042 discarded_at）不可再发布——
    否则「废弃=隐藏」与「可发新版」冲突（发出去的新版在检索/导出面恒不可见，
    治理上是个黑洞：操作者看到发布成功、顾客永远查不到）。
    """
    if version.published_at is not None:
        return False
    if asset.discarded_at is not None:
        return False
    return asset.status == PENDING_REVIEW or (
        asset.status == PUBLISHED and asset.current_published_version_id is not None
    )


# ---------- 生命周期出口闸门与对象存储侧副作用（ADR 0042；纯函数便于单测） ----------


def _can_replace_version_bytes(asset: Asset, version: AssetVersion) -> bool:
    """换字节闸门（纯谓词）：仅未发布且非当前指针的版本可换（待人洗修订版/
    新登记版均属此类）；published_at 已非空或指针所指（线上版）恒 False。
    第 84 刀：已废弃资产（0042）恒 False——改字节对隐藏资产无意义，且留写口
    会与「废弃=只读终态」的口径冲突。"""
    if asset.discarded_at is not None:
        return False
    return version.published_at is None and version.id != asset.current_published_version_id


def _is_revision(asset: Asset, version: AssetVersion) -> bool:
    """未发布版是否「修订」而非新资产首发 v1（纯谓词）：version_no>1，或资产
    曾发布过（指针非空——指针只随发布设置、从不清空）。v1 未发布的普通资产
    不算修订：放弃它不是本端点的事，409 引导走「废弃」。"""
    return version.version_no > 1 or asset.current_published_version_id is not None


def _can_discard_asset(asset: Asset, *, has_published_version: bool) -> bool:
    """废弃闸门（纯谓词）：已接入（机洗失败）且从未发布（指针空且无任何
    published_at 非空的版本）才可废弃；已发布历史（权威证据）不可抹。"""
    return (
        asset.status == INGESTED
        and asset.current_published_version_id is None
        and not has_published_version
    )


def _swap_version_bytes(
    storage: ObjectStorage, kind: str, version: AssetVersion, data: bytes
) -> str:
    """换字节的对象存储侧（0003 键不复用 + 0042 孤儿清理首接线）：新键先写、
    旧键后删（未发布版的旧键从此无引用），版本行改指新键。副作用收口在
    一个函数里，便于单测钉死「新键写、旧键删」调用序。"""
    new_key = make_object_key(kind, data)
    storage.put_bytes(new_key, data)
    storage.delete(version.object_key)
    version.object_key = new_key
    return new_key


def _key_suffix(object_key: str) -> str | None:
    """对象键的扩展名（不含点）；没有则 None（调用方落回按 kind 兜底）。

    修订/复制字节时用来把**源对象的扩展名**带过去（ADR 0047 §4：键必须与字节
    一致）。basename 里没有点、或点后为空，都当没有扩展名。
    """
    name = object_key.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    suffix = name.rsplit(".", 1)[-1]
    return suffix or None


def _delete_version_bytes(storage: ObjectStorage, versions: Sequence[AssetVersion]) -> None:
    """删一组版本的对象字节（放弃修订/废弃资产共用；delete 幂等，键不存在
    不报错）。已发布版本的键永不进本函数（两个调用方都只喂未发布版）。"""
    for v in versions:
        storage.delete(v.object_key)


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
    """机洗失败就地重试（0012 任务不是中台对象，无任务表）：仅已接入态可重试。

    与登记同口径按种类重跑：dialogue 重跑 LLM QA 抽取（LLM 故障恢复后点此推进
    待人洗），文档重跑正则抽取。成功清 last_error。
    """
    del operator
    asset = _get_asset_or_404(db, asset_id)
    # 第 84 刀（评审 P1）：废弃资产不可重试——否则 discarded -> pending_review，
    # 而此后 publish 拒（新闸）、再 discard 拒（要求 INGESTED）、全仓无 un-discard
    # = 永久隐藏僵尸；且此形态下 asset_view 的 can_publish 会显示「可发布」。
    if asset.discarded_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已废弃的资产不能再重试",
        )
    if asset.status != INGESTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有已接入（机洗失败）的资产可以重试，当前状态: {asset.status}",
        )
    version = _latest_version_or_404(db, asset)
    product = _product_or_none(db, asset)
    # 字段集与登记同口径按 kind 分派：dialogue 重跑 LLM QA 抽取，文档重跑正则（第 12 刀）
    field_names = machine_wash_field_names(asset.kind, product)
    object_key = version.object_key
    # P1#2（第 16 刀）：前面按 404 闸门的读取已 autobegin 只读事务——dialogue
    # 重试的 LLM 等待（≤20s）不得 idle-in-transaction 占连接（对齐登记/第 11 刀纪律）
    db.commit()
    try:
        extracted = run_machine_wash(storage, object_key, field_names, asset.kind)
        version.extracted_fields = extracted
        asset.status = PENDING_REVIEW
        asset.last_error = None
    except (MachineWashError, FileNotFoundError) as exc:
        asset.last_error = str(exc)[:500] or exc.__class__.__name__
    db.commit()
    db.refresh(asset)
    db.refresh(version)
    return to_asset_detail(db, asset)


def _validate_qa_pairs_payload(value: Any) -> list[dict[str, str]]:
    """qa_pairs 人洗载荷（第 12 刀）：形状校验用 machine_wash.validate_qa_pairs
    共享口径（与 LLM 输出解析同一函数，禁两处各写），坏形状转 422。"""
    try:
        return validate_qa_pairs(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


def _mask_confirmed_value(field: str, value: Any) -> Any:
    """0038 修订（第 21 刀，审计刀 4 P0 簇出口 2）：字节不动、出口必掩——
    人洗 confirmed 值落库前的 PII 打码收口（validate 之后、写回之前）。

    - qa_pairs：每项 q/a 过 `machine_wash.redact`（复用第 17 刀函数，不复制
      正则——同一口径同时守「送厂商 prompt」与「入索引切块」两个面）；
    - 文档字符串值：整值过 redact（转写类文档手填值也可能带手机号/邮箱）。
    纯函数便于单测钉死；redact 幂等且只会收缩或等长（掩码不产空串），
    故不会把合法非空值掩成违反 0009 空串闸门的形态。
    """
    if field == QA_FIELD:
        assert isinstance(value, list)  # 上游 _validate_qa_pairs_payload 已保证形状
        return [{"q": redact(p["q"]), "a": redact(p["a"])} for p in value]
    assert isinstance(value, str)
    return redact(value)


@router.patch("/{asset_id}/versions/{version_no}/fields", response_model=VersionOut)
def confirm_fields(
    asset_id: int,
    version_no: int,
    body: dict[str, Any],
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> VersionOut:
    """人洗：确认机洗值/补填弃权字段。闸门看版本未发布；已接入仍拒绝。

    合法字段集按种类分派（ADR 0035）：dialogue -> qa_pairs（数组值，逐项
    q/a 禁空串，空数组=确认没有 QA）；文档 -> 所挂商品 spec_schema keys
    （字符串值，禁空串仍沿用）。确认值一律 source=human。
    """
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
    # 合法字段集与登记/机洗同一分派口径（按 kind；非 dialogue 的 qa_pairs 撞名字段同样拒绝）
    allowed = machine_wash_field_names(asset.kind, product)
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"字段不在该资产的合法字段集合内: {unknown}",
        )
    blank = sorted(f for f, v in body.items() if isinstance(v, str) and not v.strip())
    if blank:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"禁止空字符串冒充已抽取（0009）: {blank}",
        )
    merged = dict(version.confirmed_fields)
    for field, value in body.items():
        # 0038 修订（第 21 刀，审计刀 4 P0 簇出口 2）：字节不动、出口必掩——
        # 人洗 confirmed 值在 validate 之后、写回 confirmed_fields 之前统一过
        # redact（字符串值与 qa_pairs 每项 q/a，掩码收在 _mask_confirmed_value
        # 纯函数）。confirmed 是发布切块入索引的唯一来源（0010），写回点收口
        # 即「既有切块入口随 confirmed 数据天然干净」，手填手机号不再裸进
        # 索引与下游 prompt/回答。
        if field == QA_FIELD:
            merged[field] = {
                "value": _mask_confirmed_value(field, _validate_qa_pairs_payload(value)),
                "source": "human",
            }
        else:
            if not isinstance(value, str):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"字段值须为字符串（qa_pairs 除外，其为数组）: {field}",
                )
            merged[field] = {
                "value": _mask_confirmed_value(field, value.strip()),
                "source": "human",
            }  # 改动丢掉 inherited
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
    # 必填闸门仅种类=文档且挂商品时启用（CONTEXT「必填字段」词条；0019）——
    # 图片/视频/对话/素材没有规格必填（素材刀激活的潜伏偏离，第 17 刀修复）
    schema: dict[str, Any] = {}
    if asset.kind == "document" and product is not None:
        schema = dict(product.spec_schema)
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
            storage, version.object_key, asset.kind, confirmed, extracted
        )
    except ChunkingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"无法切块入检索索引，发布已中止: {exc}",
        ) from exc
    version.published_at = datetime.now(UTC)
    asset.status = PUBLISHED
    asset.current_published_version_id = version.id
    # 发布=验证快照（第 39 刀保鲜语义）：线上口径以本次字节为准，即最近一次
    # 验证时点；之后可在治理台「重新验证」（verify 端点）刷新。超 STALE_DAYS
    # 天未再验证的资产块在检索侧降权；NULL 恒不降权（保守裁决，retrieval.is_stale）。
    asset.last_verified_at = version.published_at
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


@router.post("/{asset_id}/verify", response_model=AssetDetail)
def verify_asset(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetDetail:
    """重新验证（第 39 刀保鲜）：仅已发布资产可验证（否则 409）——刷新
    last_verified_at=now + audit 一行（action=verify，命名先例 discard_*）。

    Guru 验证语义的最小版：操作者核过线上口径仍成立，验证时点即保鲜起点；
    检索侧对超 STALE_DAYS 天未再验证的资产块降权（retrieval.is_stale）。
    只动保鲜元数据：不改状态机、不动版本字节，audit 留痕到当前已发布版本。
    """
    asset = _get_asset_or_404(db, asset_id)
    if asset.current_published_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有已发布资产可以重新验证，当前状态: {asset.status}",
        )
    published = db.get(AssetVersion, asset.current_published_version_id)
    if published is None:  # pragma: no cover - 指针完整性由发布事务保证
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前已发布版本缺失，无法重新验证",
        )
    asset.last_verified_at = datetime.now(UTC)
    db.add(
        AuditLog(
            operator_id=operator.id,
            asset_id=asset.id,
            version_no=published.version_no,
            action="verify",
        )
    )
    db.commit()
    db.refresh(asset)
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
    # 修订沿用**源版本对象的扩展名**（第 46 刀 ADR 0047 §4：键必须与字节一致）：
    # 无源录像的旧切片字节是时间码文本、键是 .txt，按 kind 兜底会写出
    # 「.mp4 键装文本字节」——正是本刀在 PUT …/bytes 上堵掉的同一类不一致。
    object_key = make_object_key(asset.kind, content_bytes, suffix=_key_suffix(published.object_key))
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


# ---------- 生命周期出口三件（第 28 刀/ADR 0042：操作者的「开错了/传错了」退路） ----------


@router.put("/{asset_id}/versions/{version_no}/bytes", response_model=AssetDetail)
def replace_version_bytes(
    asset_id: int,
    version_no: int,
    file: Annotated[UploadFile, File()],
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """修订/待人洗版换字节（0042）：上传新正文替换该版内容——新对象键写入、
    旧修订键字节删除（孤儿清理，storage.delete 首个接线方）、重跑机洗。

    闸门：版本须存在且未发布且非当前指针（线上字节永不动，409）；**video 资产
    直接 409**（第 46 刀：正文由 transcript 字段承载、字节是切片，换字节会写出
    「.mp4 键装文本」并清空预置字段——见下面 816 行那段）。extracted 按新字节
    重算（字段集与登记同口径按 kind+product 分派）、confirmed 保留（继承/人洗
    确认值不逼重存）。上传校验对齐登记（类型/2MB/空文件），**类型闸在 video 闸
    之前**：给视频资产传 mp4 会先吃 415，传文本才 409。

    同步 def（同 CSV/重试先例）：dialogue 重跑机洗含 LLM（asyncio.run，≤20s），
    必须跑在线程池线程而非事件循环；字节换序在 commit 后、机洗窗口外（P1#2
    不 idle-in-transaction）。机洗失败不停在半换状态：字节已换是事实，资产按
    登记同口径停 ingested 存 last_error（修订中资产 status/指针不动，线上
    继续 v1，可重传或重试）。
    """
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"仅接受 {' / '.join(sorted(ALLOWED_CONTENT_TYPES))}，收到: {file.content_type}",
        )
    data = file.file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文档超过 2MB 上限"
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="空文件不能上传"
        )

    asset = _get_asset_or_404(db, asset_id)
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.version_no == version_no
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产版本不存在")
    if asset.kind == "video":
        # 第 46 刀（ADR 0047）：视频资产的正文由 transcript 字段承载、字节是切出的
        # mp4 片段。换字节在这里有两重坏处：①上传的是文本，而 video 的对象键按
        # kind 走 .mp4——键与字节不一致；②video 机洗字段集恒空，重算会把预置的
        # transcript 整体覆盖成空集，检索块静默归零。故直接拒绝（想要别的片段，
        # 去切片页重拣）。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="视频资产的正文来自转写字段、字节是切片，不支持换正文；请回切片页重拣",
        )
    if not _can_replace_version_bytes(asset, version):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有待人洗（未发布）的版本可以换正文，线上版本字节不可触碰",
        )
    product = _product_or_none(db, asset)
    # 字段集在 commit 前算完（读 spec_schema 会 autobegin，别把只读事务
    # 留进机洗窗口）；confirmed 不动——重跑只重算 extracted
    field_names = machine_wash_field_names(asset.kind, product)
    _swap_version_bytes(storage, asset.kind, version, data)
    db.commit()  # P1#2：字节换序先落库，机洗（dialogue 含 LLM ≤20s）不持事务

    try:
        extracted = run_machine_wash(storage, version.object_key, field_names, asset.kind)
        version.extracted_fields = extracted
        if asset.status != PUBLISHED:
            asset.status = PENDING_REVIEW
        asset.last_error = None
    except (MachineWashError, FileNotFoundError) as exc:
        asset.last_error = str(exc)[:500] or exc.__class__.__name__
        if asset.status != PUBLISHED:
            asset.status = INGESTED
    db.commit()
    db.refresh(asset)
    db.refresh(version)
    return to_asset_detail(db, asset)


@router.post("/{asset_id}/revisions/discard", response_model=AssetDetail)
def discard_revision(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """放弃修订（0042）：删未发布修订版（清字节 + 删版本行），解锁回滚与
    再开修订（无状态需清——两者闸门本就只看「有无未发布版」）。

    无未发布版 409；未发布版是 v1 新资产（从未发布过的普通资产）时 409
    引导走「废弃」。audit 记 discard_revision（谁/何时/哪版）。
    """
    asset = _get_asset_or_404(db, asset_id)
    version = _unpublished_version(db, asset)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="没有未发布修订可放弃",
        )
    if not _is_revision(asset, version):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="未发布的 v1 是新资产首发而不是修订；机洗失败的传错文件请走「废弃」",
        )
    version_no = version.version_no
    _delete_version_bytes(storage, [version])
    db.delete(version)
    db.add(
        AuditLog(
            operator_id=operator.id,
            asset_id=asset.id,
            version_no=version_no,
            action="discard_revision",
        )
    )
    db.commit()
    db.refresh(asset)
    return to_asset_detail(db, asset)


@router.post("/{asset_id}/discard", response_model=AssetDetail)
def discard_asset(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> AssetDetail:
    """废弃失败资产（0042）：仅「已接入（机洗失败）且从未发布（所有版本
    published_at 全空且指针空）」——已发布历史（含指针已回退）不可抹，409。

    资产行不删：discarded_at 置标记（迁移 0014；列表默认过滤，不是第四态），
    全部版本字节清掉（每键 storage.delete；版本行保留作审计锚，键已悬空）。
    audit 记 discard_asset。检索/血缘无影响：从未发布本就不进索引。
    """
    asset = _get_asset_or_404(db, asset_id)
    versions = list(
        db.scalars(select(AssetVersion).where(AssetVersion.asset_id == asset.id))
    )
    has_published = any(v.published_at is not None for v in versions)
    if not _can_discard_asset(asset, has_published_version=has_published):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "只有机洗失败（已接入）且从未发布过的资产可以废弃，"
                f"已发布历史不可抹，当前状态: {asset.status}"
            ),
        )
    asset.discarded_at = datetime.now(UTC)
    _delete_version_bytes(storage, versions)
    latest_no = max((v.version_no for v in versions), default=1)
    db.add(
        AuditLog(
            operator_id=operator.id,
            asset_id=asset.id,
            version_no=latest_no,
            action="discard_asset",
        )
    )
    db.commit()
    db.refresh(asset)
    return to_asset_detail(db, asset)


# ---------- 读接口 ----------


@router.get("", response_model=list[AssetOut])
def list_assets(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    # 第 19 刀/ADR 0040 最小侵入加种类过滤（与 status 并存）：销售考核题库推导
    # 只取已发布对话；不限枚举值，未知 kind 自然得空列表
    kind: Annotated[str | None, Query()] = None,
    # 第 51 刀：来源过滤——工作队列首屏被导入货淹掉（评论导入 200 条里 180 条
    # 进待洗队列），运营要有办法只看自己传的/只看某一来源。取值受 SOURCE_KINDS
    # 约束（未知 422，别让拼错的来源静默返回空列表）。
    # 注：控制台内当前是**客户端过滤**（一次拉全量、数据量小）；这个参数是服务端
    # 能力（API 消费者/将来分页时用），接口式用例钉着它。
    source_kind: Annotated[str | None, Query()] = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[AssetOut]:
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    if status_filter is not None and status_filter not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status 只能是 {'/'.join(sorted(_VALID_STATUSES))}",
        )
    if source_kind is not None and source_kind not in SOURCE_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"source_kind 只能是 {'/'.join(sorted(SOURCE_KINDS))}",
        )
    # 0042：discarded 是治理动作后的隐藏标记——列表（含各状态页签）默认
    # 不可见；详情/血缘按 id 直达不受影响，检索本就只有已发布
    query = select(Asset).where(Asset.discarded_at.is_(None)).order_by(Asset.id.desc())
    if kind is not None:
        query = query.where(Asset.kind == kind)
    if source_kind is not None:
        query = query.where(Asset.source_kind == source_kind)
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


@router.get("/{asset_id}/lineage", response_model=AssetLineageOut)
def get_asset_lineage(
    asset_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AssetLineageOut:
    """血缘视图（第 20 刀/ADR 0026）：从哪来/版本与审计/被谁用过（引用/写回/
    考核）——资产详情上的派生拼装，只读、零新表零写路径，不进检索不进 MCP。

    数据源全在既有表：audit_log（时间线；写回=publish/rollback 事件——0010/
    0034 两类移指针事务都写回商品，fields 从 asset_versions.confirmed_fields
    按版本号如实派生）、service_messages.citations JSONB containment 下推
    （引用样例+同条件计数）、coach_records.question_key（考核抽题）；另有
    本资产版本行一次轻查询供 fields 派生。origin.created_at 恒 null（assets
    无登记时间列）。响应形状与截断规则见 services/lineage.py。
    """
    del operator  # 读接口同样要求登录（CONTEXT.md：控制台=登录后的人机界面）
    asset = _get_asset_or_404(db, asset_id)
    return fetch_asset_lineage(db, asset)


@router.get("/{asset_id}/versions/{version_no}/text")
def get_version_text(
    asset_id: int,
    version_no: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> PlainTextResponse:
    """版本正文（对象存储字节 UTF-8 解码，text/plain；ADR 0003 字节只住对象存储）。

    操作者面人洗视图：第 12 刀起 dialogue 资产人洗必须看得见转写正文（ADR 0035
    后果）。复用 read_version_text 服务函数（与 MCP get_asset 同一取数路径）；
    对象缺失/非 UTF-8 属存储异常 -> 409。

    0038 修订（第 21 刀）明确豁免：本端点**不打码**——「字节不动、出口必掩」
    里版本正文端点是操作者面的原文回放通道（治理语境，v1 单操作者可接受；
    多角色时再收紧）。打码只发生在其余出口（prompt/confirmed 落库/coaching/
    MCP export/血缘），永不回写存储，人洗对照转写必须能看到原文。
    """
    del operator  # 读接口要求登录
    asset = _get_asset_or_404(db, asset_id)
    version = db.scalar(
        select(AssetVersion).where(
            AssetVersion.asset_id == asset.id, AssetVersion.version_no == version_no
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产版本不存在")
    try:
        text = read_version_text(db, storage, version)
    except VersionTextError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PlainTextResponse(text)
