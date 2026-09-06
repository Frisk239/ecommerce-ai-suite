"""登记共享服务（0013 没有字节不能登记）：文档登记与会话回流登记的同一骨架。

put_bytes -> Asset(ingested) -> flush 拿主键 -> AssetVersion(v1) -> 同步机洗
try/except（成功推进 pending_review；失败停 ingested 存 last_error，照样落库）。
路由层只保留各自的入参校验与外围状态（如会话置 registered）；本函数不
commit——事务边界归调用方（回流登记要把会话状态变更并进同一事务）。
"""

import hashlib
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, Product
from suite_api.services.machine_wash import MachineWashError, run_machine_wash
from suite_api.services.publishing import schema_field_names
from suite_platform.storage import ObjectStorage

# 资产状态机前两态（登记入口）：ingested=已接入 / pending_review=待人洗。
# 第三态 published 留在资产路由（发布动作所在地）。
INGESTED = "ingested"
PENDING_REVIEW = "pending_review"


def register_asset(
    db: Session,
    storage: ObjectStorage,
    *,
    kind: str,
    title: str | None,
    content_bytes: bytes,
    filename: str | None,
    product_id: int | None,
) -> Asset:
    """登记资产 + v1 版本（含同步机洗推进），返回 asset（未 commit）。

    - 字节先落对象存储，对象键 = {documents|dialogue}/{uuid}/{sha256前16}.txt；
      filename 不参与键（ADR 0003 每版一把键，扩展名固定 .txt），仅作为登记
      入口的来源信息保留在签名里。
    - 机洗字段集由所挂商品推导：不挂商品（如 dialogue，0019 无规格必填）
      -> 空集，机洗只解析文本 -> 直接待人洗。
    - product_id 给了但不存在 -> 404（在字节落库前失败，与路由原校验同口径）。
    """
    product = None
    if product_id is not None:
        product = db.get(Product, product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    prefix = "dialogue" if kind == "dialogue" else "documents"
    digest = hashlib.sha256(content_bytes).hexdigest()[:16]
    object_key = f"{prefix}/{uuid4().hex}/{digest}.txt"
    storage.put_bytes(object_key, content_bytes)

    asset = Asset(
        kind=kind,
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
    return asset
