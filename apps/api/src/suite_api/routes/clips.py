"""直播切片路由（第 18 刀，ADR 0014/0015/0039）：候选列表与批量拣选登记。

两端点全操作者 cookie 鉴权（拣选是操作者动作，0016 控制台=登录后的人机界面）。
候选不是中台对象（0014）：GET 视图带商品名与 registered_asset_id 回执锚
（已登记行的「A-xxxx」跳治理台）。POST pick 的批量原子性、404/409 判定在
services/clips.pick_candidates——校验先于第一个字节落库，批量含已登记整体
409；空数组/非数组 ids 由 pydantic（min_length）转 422。登记返回资产列表
（AssetOut，kind=video/来源=切片拣选），id 供前端把卡片换成「已登记」。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import ClipCandidate, Operator, Product
from suite_api.services.asset_view import (
    AssetOut,
    load_products,
    published_version_nos,
    revising_asset_ids,
    to_asset_out,
)
from suite_api.services.clips import pick_candidates
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/clips", tags=["clips"])


class ClipCandidateOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    status: str  # pending | registered（单向，0039）
    timecode_start: str
    timecode_end: str
    transcript: str
    source_video_label: str
    registered_asset_id: int | None
    created_at: datetime


class ClipPickIn(BaseModel):
    ids: Annotated[list[int], Field(min_length=1)]


def _product_name(db: Session, product_id: int) -> str:
    """product_id 有 FK 保证行存在；视图仍防漂（缺名回退占位，不 500）。"""
    product = db.get(Product, product_id)
    return product.name if product is not None else "—"


def _to_out(candidate: ClipCandidate, product_name: str) -> ClipCandidateOut:
    return ClipCandidateOut(
        id=candidate.id,
        product_id=candidate.product_id,
        product_name=product_name,
        status=candidate.status,
        timecode_start=candidate.timecode_start,
        timecode_end=candidate.timecode_end,
        transcript=candidate.transcript,
        source_video_label=candidate.source_video_label,
        registered_asset_id=candidate.registered_asset_id,
        created_at=candidate.created_at,
    )


@router.get("/candidates", response_model=list[ClipCandidateOut])
def list_candidates(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[ClipCandidateOut]:
    """候选卡片列表（种子 mock，0039）：id 升序，已登记行带回执锚。"""
    del operator  # 读接口同样要求登录
    candidates = list(db.scalars(select(ClipCandidate).order_by(ClipCandidate.id)))
    return [_to_out(c, _product_name(db, c.product_id)) for c in candidates]


@router.post("/candidates/pick", response_model=list[AssetOut])
def pick(
    body: ClipPickIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> list[AssetOut]:
    """批量拣选登记：候选转写字节入库，各登记为 kind=视频/来源=切片拣选资产
    （已接入→机洗弃权推进待人洗，0039）；候选置 registered 终态。不造任务
    （0015）。"""
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    assets = pick_candidates(db, storage, body.ids)
    products = load_products(db, assets)
    version_nos = published_version_nos(db, assets)
    revising_ids = revising_asset_ids(db, assets)
    return [to_asset_out(a, products, version_nos, revising_ids) for a in assets]
