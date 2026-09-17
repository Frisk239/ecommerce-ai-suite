"""直播切片路由（第 18 刀，ADR 0014/0015/0039；第 46 刀真链路；第 93 刀云转写）。

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

POST /recordings/{id}/transcribe（第 93 刀，ADR 0050）：云 ASR 把整段录像转成
**停顿聚合的候选**落 pending（人工拣选闸门保留）。无 key 409（fail-closed：
不建客户端、不发请求）、无音轨 422、云转写失败 502、已有未拣选 cloud 候选 409
（拒绝重跑，回执带现有条数——避免重复堆候选；全被拣选/登记后可再生成一批）。
GET /asr/status 给前端「无 key 时禁用按钮并提示」的判据。
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import ClipCandidate, ClipRecording, Operator, Product
from suite_api.services import asr as asr_service
from suite_api.services import vlm as vlm_service
from suite_api.services.asset_view import (
    AssetOut,
    load_product_names,
    load_products,
    published_version_nos,
    revising_asset_ids,
    to_asset_out,
)
from suite_api.services.clips import (
    bind_candidates,
    pick_candidates,
    register_recording,
    split_pending_ids,
)
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/clips", tags=["clips"])

logger = logging.getLogger(__name__)

# 源录像上传上限 200MB（原始录像可远大于 2MB 文档上限）；仅按扩展名收 .mp4
MAX_RECORDING_BYTES = 200 * 1024 * 1024


class ClipRecordingOut(BaseModel):
    """源录像视图（列表与候选行里的 recording 字段共用）：**只有这四个字段**。"""

    id: int
    label: str
    size_bytes: int
    created_at: datetime


class ClipRecordingUploadOut(ClipRecordingOut):
    """上传回执（第 49 刀）：多一个 bound_count——本次顺手绑定的待拣候选条数，
    是后端真值（前端不再拿上传前的候选数猜）。"""

    bound_count: int


class ClipBindIn(BaseModel):
    """改绑请求体（第 49 刀）：candidate_ids 缺省/null = 全部待拣候选。"""

    candidate_ids: Annotated[list[int], Field(min_length=1)] | None = None


class ClipBindOut(BaseModel):
    recording_id: int
    label: str
    bound_count: int


# 源录像列表上限（第 49 刀）：够选即可，不做分页（录像不是中台对象，数量级=演示）
RECORDING_LIST_LIMIT = 20


class ClipCandidateOut(BaseModel):
    id: int
    # 第 93 刀起可空：云转写候选按录像整段生成，句子里没有商品归属（不编造）
    product_id: int | None
    product_name: str
    status: str  # pending | registered（单向，0039）
    timecode_start: str
    timecode_end: str
    transcript: str
    # 转写来源（第 93 刀）：cloud/local/manual，只读标注（来源是既成事实）
    transcript_source: str
    source_video_label: str
    # 绑定的源录像（第 46 刀）：null=无源录像（拣选走时间码文本旧路径）
    recording: ClipRecordingOut | None
    registered_asset_id: int | None
    created_at: datetime


class ClipPickIn(BaseModel):
    ids: Annotated[list[int], Field(min_length=1)]


class ClipTranscribeIn(BaseModel):
    """转写请求体（第 93 刀）：product_id 可选——整段录像只讲一件商品时可顺手
    归属；不给则候选无商品（ASR 从句子里判不出归属，不猜）。"""

    product_id: int | None = None


class ClipTranscribeOut(BaseModel):
    """转写回执（第 93 刀）：真值三件套 + 上限合并附注。

    - ``candidates_created``：本次落库的 pending 候选条数；
    - ``segments``：ASR 返回的句级段数（聚合前的原始句数）；
    - ``duration_ms``：同步请求耗时（含提音轨与全部云请求）；
    - ``note``：上限合并/无语音等如实说明（未触发为 null）。
    """

    candidates_created: int
    segments: int
    duration_ms: int
    note: str | None = None


class ClipAsrStatusOut(BaseModel):
    """ASR 配置状态（第 93 刀）：前端据此禁用「自动转写」并给提示。

    **只回布尔**：不回 base_url/model（配置细节不进前端）；key 本身更不回。
    """

    configured: bool


class ClipFrameStatusOut(BaseModel):
    """洗帧 VLM 配置状态（第 94c 刀，ADR 0053）：前端据此禁用资产详情的
    「洗帧到素材库」并给提示。只回布尔（同 asr/status 口径）；后端仍是唯一闸
    ——即使前端被绕过，候选端点自己 409（fail-closed）。"""

    configured: bool


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
        transcript_source=candidate.transcript_source,
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


@router.post("/recordings", response_model=ClipRecordingUploadOut, status_code=status.HTTP_201_CREATED)
def upload_recording(
    file: Annotated[UploadFile, File()],
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> ClipRecordingUploadOut:
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
    #
    # **同步 def + file.file.read**（与 PUT …/bytes 同形，审计刀 9 P1）：路由若是
    # async，200MB 的 SHA256 与整块落盘会**跑在事件循环上**，把同一循环里的顾客
    # SSE 流掐住；同步路由由 FastAPI 丢进线程池，阻塞的只是那个工作线程。
    data = file.file.read(MAX_RECORDING_BYTES + 1)
    if len(data) > MAX_RECORDING_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="源录像超过 200MB 上限"
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="空文件不能作为源录像")
    recording, bound_count = register_recording(
        db, storage, label=(filename or "未命名录像")[:200], content_bytes=data
    )
    logger.info(
        "源录像已登记: id=%s label=%s size=%s bound=%s",
        recording.id,
        recording.label,
        recording.size_bytes,
        bound_count,
    )
    return ClipRecordingUploadOut(
        id=recording.id,
        label=recording.label,
        size_bytes=recording.size_bytes,
        created_at=recording.created_at,
        bound_count=bound_count,
    )


@router.get("/recordings", response_model=list[ClipRecordingOut])
def list_recordings(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[ClipRecordingOut]:
    """源录像列表（第 49 刀）：created_at 降序、最多 20 条。

    「改绑到哪一份」需要先看得见有哪些份——没有列表就只有「刚上传的那份」可选，
    等于把「绑错锁死」换成「只能改成最新一份」。录像不是中台对象（第 46 刀裁决
    1）：不做分页/搜索/删除，列表只是选择器的数据源。
    """
    del operator  # 读接口同样要求登录
    rows = db.scalars(
        select(ClipRecording)
        .order_by(ClipRecording.created_at.desc(), ClipRecording.id.desc())
        .limit(RECORDING_LIST_LIMIT)
    )
    return [
        ClipRecordingOut(
            id=r.id, label=r.label, size_bytes=r.size_bytes, created_at=r.created_at
        )
        for r in rows
    ]


@router.post("/recordings/{recording_id}/bind", response_model=ClipBindOut)
def bind_recording(
    recording_id: int,
    body: ClipBindIn | None = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ClipBindOut:
    """把待拣候选改绑到指定源录像（第 49 刀，**修订第 46 刀裁决 2**）。

    语义：`candidate_ids` 缺省/null = 全部待拣候选；给了就只改这些。已绑的候选
    **允许改绑**（这条就是本刀要解锁的：原来「绑过就不许改」把绑错变成终态）；
    已登记候选**一律不动**（回执锚已定），指定了就 409 且一行不写。返回后端真值
    `bound_count`。
    """
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    recording = db.get(ClipRecording, recording_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源录像不存在")
    candidate_ids = body.candidate_ids if body is not None else None
    if candidate_ids is not None:
        found, registered = split_pending_ids(db, list(dict.fromkeys(candidate_ids)))
        if len(found) != len(set(candidate_ids)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="切片候选不存在"
            )
        if registered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"切片候选 {registered[0]} 已登记为资产，源录像绑定不可追改",
            )
    bound = bind_candidates(db, recording.id, candidate_ids=candidate_ids)
    db.commit()
    logger.info(
        "源录像改绑: recording=%s label=%s bound=%s", recording.id, recording.label, bound
    )
    return ClipBindOut(recording_id=recording.id, label=recording.label, bound_count=bound)


@router.get("/asr/status", response_model=ClipAsrStatusOut)
def asr_status(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
) -> ClipAsrStatusOut:
    """ASR 配置状态（第 93 刀）：前端「无 key 时禁用按钮并提示」的判据。

    只回布尔（配置细节与密钥都不进前端）；后端仍是唯一闸——即使前端被绕过，
    转写端点自己 409（fail-closed）。
    """
    del operator  # 读接口同样要求登录
    return ClipAsrStatusOut(configured=asr_service.is_configured())


@router.get("/frames/status", response_model=ClipFrameStatusOut)
def frame_wash_status(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
) -> ClipFrameStatusOut:
    """洗帧 VLM 配置状态（第 94c 刀）：帧打分复用 94a 的 VLM 客户端（同一把
    ``VLM_API_KEY``），此端点给前端禁用判据。放在 clips 路由是因为洗帧属直播/
    切片家族（入口在资产详情页，与 asr/status 同居一族）。"""
    del operator  # 读接口同样要求登录
    return ClipFrameStatusOut(configured=vlm_service.is_configured())


@router.post("/recordings/{recording_id}/transcribe", response_model=ClipTranscribeOut)
def transcribe_recording(
    recording_id: int,
    body: ClipTranscribeIn | None = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> ClipTranscribeOut:
    """自动转写（第 93 刀，ADR 0050）：整段录像 → 停顿聚合候选落 pending。

    判定次序与码位：录像不存在 404；``ASR_API_KEY`` 为空 409（**不建客户端、
    不发请求**——诚实拒绝，人工填 transcript 的现状不变）；该录像已有未拣选的
    cloud 候选 409（带现有条数：重跑不是追加，避免重复堆候选；全部拣选/登记后
    可再生成一批）；``product_id`` 给了但不存在 404；录像无音轨 422；云转写
    失败/无句级时间戳 502。成功 200 + 真值回执。

    同步执行（路由是同步 def，跑在线程池：ffmpeg 与云请求都是阻塞调用，不占
    事件循环）；单请求云超时 120s（超过转 502，可再点一次）。
    """
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    recording = db.get(ClipRecording, recording_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源录像不存在")
    if not asr_service.is_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ASR 未配置（ASR_API_KEY 为空）：无法自动转写，可人工填写转写后拣选",
        )
    existing = asr_service.pending_count(db, recording.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"该录像已有 {existing} 条未拣选的云转写候选：先拣选或改绑处理完再重跑，"
                "避免重复堆候选"
            ),
        )
    product_id = body.product_id if body is not None else None
    if product_id is not None and db.get(Product, product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    # 快照 + 收口事务再动合成（P1#2 纪律，与 pick_candidates 的「收口事务再动
    # 子进程」同口径）：ffmpeg + 云请求最长 ~120s，带着上面的校验读等它 =
    # idle-in-transaction 占池连接；rollback 会过期 ORM 实例，故先取只读快照。
    ref = asr_service.RecordingRef.of(recording)
    db.rollback()
    started = time.monotonic()
    try:
        outcome = asr_service.transcribe_recording(db, storage, ref, product_id=product_id)
    except asr_service.ASRNotConfigured as exc:
        # 配置检查与调用之间 key 被清（进程内 settings 不变，仅防御位）
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ASR 未配置（ASR_API_KEY 为空）：无法自动转写，可人工填写转写后拣选",
        ) from exc
    except asr_service.AudioExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except asr_service.ASRUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "源录像自动转写: recording=%s candidates=%s segments=%s merged=%s duration_ms=%s",
        ref.id,
        outcome.candidates_created,
        outcome.segments,
        outcome.merged,
        duration_ms,
    )
    return ClipTranscribeOut(
        candidates_created=outcome.candidates_created,
        segments=outcome.segments,
        duration_ms=duration_ms,
        note=outcome.note,
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
