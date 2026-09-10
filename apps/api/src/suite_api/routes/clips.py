"""直播切片路由（第 18 刀，ADR 0014/0015/0039；第 46 刀真链路）。

端点全操作者 cookie 鉴权（拣选/上传源录像都是操作者动作，0016 控制台=登录后的
人机界面）。候选不是中台对象（0014）：GET 视图带商品名、registered_asset_id
回执锚（已登记行的「A-xxxx」跳治理台）与绑定的源录像信息（第 46 刀：让前端
显示「当前源录像」——放每候选的 recording 字段而非单独端点，因为绑定本就是
候选级事实，多源不同绑时也能如实呈现；前端取 pending 候选里最新的那份当
「当前源录像」）。

POST /recordings 上传源录像（.mp4，≤200MB）：录像是切片模块自有、不是资产
（裁决 1），上传即把「尚无源录像」的 pending 候选绑上（裁决 2）。

POST pick 的批量原子性、404/409 判定在 services/clips.pick_candidates——校验
先于第一个字节落库，批量含已登记整体 409；空数组/非数组 ids 由 pydantic
（min_length）转 422。有源录像的候选真切 mp4，切失败 422 且该候选保持 pending。
登记返回资产列表（AssetOut，kind=video/来源=切片拣选），id 供前端把卡片换成
「已登记」。
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import ClipCandidate, ClipRecording, Operator
from suite_api.services.asset_view import (
    AssetOut,
    load_product_names,
    load_products,
    published_version_nos,
    revising_asset_ids,
    to_asset_out,
)
from suite_api.services.clips import pick_candidates, register_recording
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/clips", tags=["clips"])

# 源录像上传上限 200MB（原始录像可远大于 2MB 文档上限）；仅按扩展名收 .mp4
MAX_RECORDING_BYTES = 200 * 1024 * 1024


class ClipRecordingOut(BaseModel):
    id: int
    label: str
    size_bytes: int
    created_at: datetime


class ClipCandidateOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    status: str  # pending | registered（单向，0039）
    timecode_start: str
    timecode_end: str
    transcript: str
    source_video_label: str
    # 绑定的源录像（第 46 刀）：null=无源录像（拣选走时间码文本旧路径）
    recording: ClipRecordingOut | None
    registered_asset_id: int | None
    created_at: datetime


class ClipPickIn(BaseModel):
    ids: Annotated[list[int], Field(min_length=1)]


def _recording_out(recording: ClipRecording | None) -> ClipRecordingOut | None:
    if recording is None:
        return None
    return ClipRecordingOut(
        id=recording.id,
        label=recording.label,
        size_bytes=recording.size_bytes,
        created_at=recording.created_at,
    )


def _to_out(candidate: ClipCandidate, product_name: str, recording: ClipRecording | None) -> ClipCandidateOut:
    return ClipCandidateOut(
        id=candidate.id,
        product_id=candidate.product_id,
        product_name=product_name,
        status=candidate.status,
        timecode_start=candidate.timecode_start,
        timecode_end=candidate.timecode_end,
        transcript=candidate.transcript,
        source_video_label=candidate.source_video_label,
        recording=_recording_out(recording),
        registered_asset_id=candidate.registered_asset_id,
        created_at=candidate.created_at,
    )


@router.get("/candidates", response_model=list[ClipCandidateOut])
def list_candidates(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[ClipCandidateOut]:
    """候选卡片列表（种子 mock，0039）：id 升序，已登记行带回执锚、带源录像。"""
    del operator  # 读接口同样要求登录
    candidates = list(db.scalars(select(ClipCandidate).order_by(ClipCandidate.id)))
    # 批取商品名（debt-2 N+1）：一次 IN 查询替代逐行 db.get，先例 asset_view.load_products
    names = load_product_names(db, {c.product_id for c in candidates})
    # 批取源录像（同 N+1 收口）：一次 IN 查询，避免逐候选 db.get
    recording_ids = {c.recording_id for c in candidates if c.recording_id is not None}
    recordings: dict[int, ClipRecording] = {}
    if recording_ids:
        recordings = {
            r.id: r for r in db.scalars(select(ClipRecording).where(ClipRecording.id.in_(recording_ids)))
        }
    return [
        _to_out(c, names.get(c.product_id, "—"), recordings.get(c.recording_id) if c.recording_id else None)
        for c in candidates
    ]


@router.post("/recordings", response_model=ClipRecordingOut, status_code=status.HTTP_201_CREATED)
async def upload_recording(
    file: Annotated[UploadFile, File()],
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> ClipRecordingOut:
    """上传源录像（第 46 刀裁决 1/2/9）：扩展名须 .mp4，≤200MB，非空。

    录像是切片模块自有的输入源（不是资产、不进检索）；登记行后把「尚无源录像」
    的 pending 候选一次绑上。返回 {id, label, size_bytes, created_at}。
    """
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    filename = file.filename or ""
    if Path(filename).suffix.lower() != ".mp4":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"源录像仅接受 .mp4 文件，收到: {filename or '(无文件名)'}",
        )
    # 有界读取（上限 +1 字节）：先读满 200MB 再判超限等于把上限白设——大文件
    # 会先吃满内存/临时盘才被 413。多读 1 字节即可判定「超了」。
    data = await file.read(MAX_RECORDING_BYTES + 1)
    if len(data) > MAX_RECORDING_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="源录像超过 200MB 上限"
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="空文件不能作为源录像")
    recording = register_recording(
        db, storage, label=(filename or "未命名录像")[:200], content_bytes=data
    )
    return ClipRecordingOut(
        id=recording.id,
        label=recording.label,
        size_bytes=recording.size_bytes,
        created_at=recording.created_at,
    )


@router.post("/candidates/pick", response_model=list[AssetOut])
def pick(
    body: ClipPickIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> list[AssetOut]:
    """批量拣选登记：有源录像的候选真切 mp4 片段（预置 transcript 字段），
    无源录像的写时间码文本；各登记为 kind=视频/来源=切片拣选资产（已接入→
    机洗弃权推进待人洗）；候选置 registered 终态。不造任务（0015）。"""
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    assets = pick_candidates(db, storage, body.ids)
    products = load_products(db, assets)
    version_nos = published_version_nos(db, assets)
    revising_ids = revising_asset_ids(db, assets)
    return [to_asset_out(a, products, version_nos, revising_ids) for a in assets]
