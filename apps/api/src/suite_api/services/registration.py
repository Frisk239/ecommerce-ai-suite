"""登记共享服务（0013 没有字节不能登记）：文档登记与会话回流登记的同一骨架。

put_bytes -> Asset(ingested) -> flush 拿主键 -> AssetVersion(v1) -> **commit**
-> 同步机洗 try/except（成功推进 pending_review；失败停 ingested 存
last_error，终态由调用方最后一次 commit 收口）。路由层只保留各自的入参
校验与外围状态（如会话置 registered）。

事务边界（第 16 刀，审计刀 3 P1#2，对齐第 11 刀 run_ask 纪律）：机洗在
dialogue 时会调 LLM 抽取 QA（至多 20s）——登记行（字节+Asset+版本）先
commit 落库释放连接，机洗绝不在持有写事务的状态下跑（不 idle-in-transaction
占池）。先提交的只是 ingested 态：「登记失败不挡字节」语义不变（机洗失败
停已接入可重试，重试端点重跑）；推进待人洗/记 last_error 的终态仍并进调用方
事务（回流登记与会话状态变更同批提交）。

source_kind（0025）：登记必填来源种类，由调用端点按语义定值（上传=upload、
回流=session_backflow），不让调用方自由填报；坏值 ValueError，路由层转 422。
"""

import hashlib
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, Product
from suite_api.services.machine_wash import QA_FIELD, MachineWashError, run_machine_wash
from suite_api.services.publishing import schema_field_names
from suite_platform.storage import ObjectStorage

# 资产三态：ingested=已接入 / pending_review=待人洗 / published=已发布。
INGESTED = "ingested"
PENDING_REVIEW = "pending_review"
PUBLISHED = "published"

# 来源八枚举（0025/ADR 0030）：登记端点语义定值，调用方不可自由填报。
# 第 50 刀增两值——真实数据集导入不再被压成「上传」（来源只活在脚本常量里的
# 那些数据，产品面上看不出「这不是我们自己传的」）：
#   review_import = 评论数据集导入（在线购物评论 200 条）
#   open_dataset  = 开放数据集（Wikidata 商品 / OpenFoodFacts 规格）
# 无 DB CHECK（应用层枚举），加值零 DDL；回填走迁移 0026。
SOURCE_KINDS = (
    "upload",
    "session_backflow",
    "clip_pick",
    "material_generated",
    "mcp_registered",
    "seed",
    "review_import",
    "open_dataset",
)


def make_object_key(kind: str, content_bytes: bytes, *, suffix: str | None = None) -> str:
    """对象键 = {documents|dialogue|clips}/{uuid}/{sha256前16}.{txt|mp4}（ADR 0003 每版一把键）。

    扩展名**跟实际字节走**（第 46 刀裁决 4；评审 P1 修正）：`suffix` 显式给定时
    以它为准——切片的真 mp4 路径传 `.mp4`、**无源录像的旧文本路径传 `.txt`**
    （否则会出现「键说 mp4、字节是文本」的不一致）。不给 suffix 时按 kind 兜底
    （video→mp4，其余→txt）。前缀照旧：video 走 clips/（ADR 0039）。
    """
    if kind == "dialogue":
        prefix = "dialogue"
    elif kind == "video":
        prefix = "clips"
    else:
        prefix = "documents"
    extension = suffix if suffix is not None else ("mp4" if kind == "video" else "txt")
    digest = hashlib.sha256(content_bytes).hexdigest()[:16]
    return f"{prefix}/{uuid4().hex}/{digest}.{extension}"


def validate_source_kind(source_kind: str) -> str:
    """来源枚举校验（纯函数便于单测）：坏值 ValueError，路由层转 422。"""
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"来源种类必须是 {'/'.join(SOURCE_KINDS)} 之一，收到: {source_kind!r}")
    return source_kind


def machine_wash_field_names(kind: str, product: Product | None) -> list[str]:
    """机洗字段集按资产种类分派（第 12 刀，ADR 0035）：对话 -> 仅 qa_pairs
    （LLM 抽取）；文档挂商品 -> 商品 spec_schema 的 keys；文档不挂商品 -> 空集。
    非 dialogue 一律滤掉 qa_pairs（防御 spec_schema 撞名：QA 是种类级语义，
    LLM 分派只认 kind，不认字段名）。

    第 18 刀（ADR 0039）加 video 分支：切片登记的字节是口语转写文本，跑商品
    规格正则会误抽——字段集恒空（即便挂了商品），弃权直接推进待人洗，与
    dialogue 无 QA 时同形。"""
    if kind == "dialogue":
        return [QA_FIELD]
    if kind == "video":
        return []
    if product is None:
        return []
    return [f for f in schema_field_names(product.spec_schema) if f != QA_FIELD]


def register_asset(
    db: Session,
    storage: ObjectStorage,
    *,
    kind: str,
    title: str | None,
    content_bytes: bytes,
    filename: str | None,
    product_id: int | None,
    source_kind: str,
    preset_fields: dict[str, str] | None = None,
    key_suffix: str | None = None,
) -> Asset:
    """登记资产 + v1 版本（含同步机洗推进），返回 asset（未 commit）。

    - source_kind 必填（0025），入口先校验——坏值在任何字节落库前失败。
    - 字节先落对象存储，对象键 = {documents|dialogue|clips}/{uuid}/{sha256前16}；
      filename 不参与键（ADR 0003 每版一把键），仅作为登记入口的来源信息保留
      在签名里。扩展名按 kind 分派（第 46 刀：video=.mp4，其余=.txt）。
    - 机洗字段集按种类分派（machine_wash_field_names）：dialogue -> qa_pairs
      （LLM 抽 QA 草稿；未配置模型=弃权降级照常待人洗，失败=停已接入可重试）；
      文档挂商品 -> spec_schema keys；文档不挂商品 -> 空集直接待人洗。
    - preset_fields（第 46 刀）：登记时就已知的正文来源（如切片候选的转写），
      {字段: 值} 在机洗之后并入 extracted_fields（source=machine，值不被机洗
      覆盖：setdefault）。video 字段集恒空（machine_wash_field_names），且有源
      录像时字节是 mp4 二进制——机洗读字节必解码失败，故「有预置字段且机洗字段
      集为空」时跳过读字节（预置字段即这批资产的机洗成果），照常推进待人洗。
    - product_id 给了但不存在 -> 404（在字节落库前失败，与路由原校验同口径）。
    - 机洗前 commit（P1#2）：LLM 等待至多 20s，不得 idle-in-transaction 占连接。
    """
    validate_source_kind(source_kind)
    product = None
    if product_id is not None:
        product = db.get(Product, product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    object_key = make_object_key(kind, content_bytes, suffix=key_suffix)
    storage.put_bytes(object_key, content_bytes)

    asset = Asset(
        kind=kind,
        status=INGESTED,
        title=title,
        product_id=product.id if product is not None else None,
        source_kind=source_kind,
    )
    db.add(asset)
    db.flush()  # 拿主键（版本行要 asset_id）
    version = AssetVersion(
        asset_id=asset.id,
        version_no=1,
        object_key=object_key,
        extracted_fields={},
        confirmed_fields={},
    )
    db.add(version)

    # 字段集在 commit 前算完（读 product.spec_schema 会 autobegin，别把只读
    # 事务留进机洗窗口）
    field_names = machine_wash_field_names(kind, product)
    # P1#2（第 16 刀）：登记行先提交——机洗（dialogue 含 LLM ≤20s）不持有
    # 事务；成功/失败的终态推进在下一个 autobegin 事务里，由调用方 commit 收口。
    # 两段式的代价：登记行 commit 后、调用方收口前进程崩溃，last_error 可能
    # 未落库（资产停 ingested，可经 retry 端点推进——「登记失败不挡字节」不破）
    db.commit()

    try:
        # 预置字段 + 空机洗字段集（第 46 刀 video 真切路径）：字节是 mp4，机洗
        # 读字节必解码失败，且空字段集本就是空操作——跳过读字节，预置字段即成果。
        if preset_fields is not None and not field_names:
            extracted: dict[str, dict] = {}
        else:
            extracted = run_machine_wash(storage, object_key, field_names, kind)
        for name, value in (preset_fields or {}).items():
            extracted.setdefault(name, {"value": value, "source": "machine"})
        version.extracted_fields = extracted  # JSONB 整体赋值，确保变更可追踪
        asset.status = PENDING_REVIEW
    except (MachineWashError, FileNotFoundError) as exc:
        asset.status = INGESTED
        asset.last_error = str(exc)[:500] or exc.__class__.__name__
    return asset
