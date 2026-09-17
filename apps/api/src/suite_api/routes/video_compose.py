"""内容成片路由（第 98b 刀，ADR 0056）：plan（选材+排版+预览+草稿，同步）/
下载（预览 mp4、剪映草稿 zip、成品 mp4）/publish（人闸门登记 material 资产）。

全部操作者 cookie 鉴权（成片是操作者动作，0016 控制台=登录后的人机界面）；
路由恒为**同步 def**（FastAPI 线程池）——plan 请求内同步执行选材+ffprobe+
TTS+ffmpeg 合成（切片探测秒级、口播 ≤60s、合成 ≤120s，与素材任务 20+20+60
的同步预算形态同源），前端超时同步放宽。

时间线候选/预览/草稿是**任务留档**（compose/ 暂存字节，不是资产）：AI 排版
结果等人审改——下剪映草稿精修或直接用预览，publish 才把文案要点经**双闸
复用**（material 的规则+LLM 质检，红线③）登记为 material 资产进素材中心
治理。**不做自动 publish**（roadmap 98b Out）。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import ComposeTask, Operator, Product
from suite_api.services import tts as tts_service
from suite_api.services import video_compose as compose_service
from suite_api.services.asset_view import load_product_names
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/video-compose", tags=["video-compose"])

# 成品上传上限（与源录像同档 200MB，服务层同值兜底）
MAX_FINAL_VIDEO_BYTES = compose_service.MAX_FINAL_VIDEO_BYTES


class ComposePlanIn(BaseModel):
    product_id: int
    # 成片模板（ADR 0056）：highlight=高光集锦（切片打头）/ product_intro=商品
    # 介绍（文案要点打头图文穿插）。默认商品介绍。
    template: str = compose_service.DEFAULT_TEMPLATE


class TtsStatusOut(BaseModel):
    configured: bool


class ComposeTaskOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    status: str
    template: str
    template_name: str
    # 时间线候选（[{type: clip|image|text, asset_id, start, dur, text?}]，秒制）
    timeline: list[dict[str, Any]]
    duration_seconds: float
    with_tts: bool
    note: str | None
    asset_id: int | None
    has_final_video: bool
    preview_url: str
    draft_url: str
    created_at: datetime


def _task_or_404(db: Session, task_id: int) -> ComposeTask:
    task = db.get(ComposeTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成片任务不存在")
    return task


def _to_out(task: ComposeTask, product_name: str) -> ComposeTaskOut:
    return ComposeTaskOut(
        id=task.id,
        product_id=task.product_id,
        product_name=product_name,
        status=task.status,
        template=task.template,
        template_name=compose_service.TEMPLATES[task.template]["name"],
        timeline=list(task.timeline or []),
        duration_seconds=task.duration_seconds,
        with_tts=task.with_tts,
        note=task.note,
        asset_id=task.asset_id,
        has_final_video=task.final_video_object_key is not None,
        preview_url=f"/api/video-compose/{task.id}/preview",
        draft_url=f"/api/video-compose/{task.id}/draft",
        created_at=task.created_at,
    )


@router.get("/tts/status", response_model=TtsStatusOut)
def tts_status(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
) -> TtsStatusOut:
    """TTS 配置状态（第 98b 刀）：前端「口播」提示判据（同 imggen/asr 先例）。

    只回布尔；后端仍是唯一闸——前端被绕过时，无 key 也只是预览无声
    （with_tts=false 如实记，不 fail 任务）。
    """
    del operator
    return TtsStatusOut(configured=tts_service.is_configured())


@router.post("/plan", response_model=ComposeTaskOut, status_code=status.HTTP_201_CREATED)
def plan(
    body: ComposePlanIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> ComposeTaskOut:
    """选材+排版+预览成片+剪映草稿（同步就地执行，返回 planned 任务）。

    - 商品 404 / 模板坏值 422 在前；无任何已发布素材 422（没有可排的版）；
    - 合成失败（ffmpeg/字体缺失）502，不落半行任务（请求态动作可整发重试）；
    - 无 TTS key：预览无声，回执 with_tts=false、note「TTS 未配置，预览无声」
      （fail-closed 不 fail 任务——口播是增值项，ADR 0056）。
    """
    del operator
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    try:
        compose_service.validate_template(body.template)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        task = compose_service.plan_compose(db, storage, product, body.template)
    except compose_service.NoMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except compose_service.RenderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except compose_service.ComposeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    names = load_product_names(db, {task.product_id})
    return _to_out(task, names.get(task.product_id, "—"))


@router.get("/tasks", response_model=list[ComposeTaskOut])
def list_tasks(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[ComposeTaskOut]:
    """成片任务列表（最新在前；操作者回看历史候选与登记回执锚）。"""
    del operator
    tasks = list(db.scalars(select(ComposeTask).order_by(ComposeTask.id.desc())))
    names = load_product_names(db, {t.product_id for t in tasks})
    return [_to_out(t, names.get(t.product_id, "—")) for t in tasks]


@router.get("/tasks/{task_id}", response_model=ComposeTaskOut)
def get_task(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ComposeTaskOut:
    del operator
    task = _task_or_404(db, task_id)
    return _to_out(task, load_product_names(db, {task.product_id}).get(task.product_id, "—"))


@router.get("/{task_id}/preview")
def get_preview(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> Response:
    """预览成片下载/播放（mp4，inline——前端 ``<video>`` 直放；操作者面）。

    服务的是任务暂存件（compose/ 前缀，不是资产）——同配图暂存预览端点先例。
    404 同文案不暴露对象键。
    """
    del operator
    task = _task_or_404(db, task_id)
    try:
        data = storage.get_bytes(task.preview_object_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="预览成片不存在"
        ) from exc
    return Response(
        content=data,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="compose-{task.id}-preview.mp4"',
            "Accept-Ranges": "none",
        },
    )


@router.get("/{task_id}/draft")
def get_draft(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> Response:
    """剪映草稿包下载（zip：draft_content/draft_meta_info/materials/，附件流）。

    解压到剪映草稿目录（com.lveditor.draft/）即可打开精修；剪映版本对包内
    相对路径不认时用「媒体重链接」指向 materials/（ADR 0056 v1 边界）。
    """
    del operator
    task = _task_or_404(db, task_id)
    try:
        data = storage.get_bytes(task.draft_object_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="剪映草稿包不存在"
        ) from exc
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="compose-{task.id}-jianying.zip"'},
    )


@router.get("/{task_id}/final")
def get_final(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> Response:
    """publish 时上传的成品 mp4 留档下载（没有上传则 404——预览即成品时用
    preview 端点）。字节是任务留档不是资产（ADR 0056）。"""
    del operator
    task = _task_or_404(db, task_id)
    if not task.final_video_object_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="该任务没有上传的成品视频"
        )
    try:
        data = storage.get_bytes(task.final_video_object_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="该任务没有上传的成品视频"
        ) from exc
    return Response(
        content=data,
        media_type="video/mp4",
        headers={"Content-Disposition": f'inline; filename="compose-{task.id}-final.mp4"'},
    )


class ComposePublishOut(BaseModel):
    task: ComposeTaskOut
    asset_id: int


@router.post("/{task_id}/publish", response_model=ComposePublishOut)
def publish(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
    final_video: Annotated[
        UploadFile | None,
        File(description="剪映导出的成品 mp4（可选；不传=用预览成片）"),
    ] = None,
) -> ComposePublishOut:
    """人闸门确认登记（planned → registered）：文案要点串联过**双闸复用**
    （material 规则四条+LLM 事实性质检，红线③）→ 登记 material 资产（kind=
    material、source_kind=upload 服务端定值、标题「{商品} · 内容成片」、挂
    商品）→ 素材中心待人洗/发布治理。

    - 双闸不过/LLM 未配置=422 不登记（fail-closed，与素材任务同语义），任务
      停在 planned 可改后重发；
    - 成品 mp4 可选上传（≤200MB、mp4 魔数校验）：字节留档在任务上（compose/
      暂存，不自动资产化——ADR 0056 Debt）；不传即认可预览成片；
    - 已登记任务重复 publish=422（状态机守卫）；**并发双 publish 的后来者
      409「任务已被确认」**（审计 19 CAS 占位，只登记一份 material）。
    """
    del operator
    task = _task_or_404(db, task_id)
    product = db.get(Product, task.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    final_bytes: bytes | None = None
    if final_video is not None:
        payload = final_video.file.read(MAX_FINAL_VIDEO_BYTES + 1)
        if len(payload) > MAX_FINAL_VIDEO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"成品视频不得超过 {MAX_FINAL_VIDEO_BYTES // (1024 * 1024)}MB",
            )
        if not payload or payload[4:8] != b"ftyp":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="成品视频必须是有效的 mp4 文件",
            )
        final_bytes = payload
    try:
        asset = compose_service.publish_compose(db, storage, task, product, final_bytes)
    except compose_service.ComposeConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except compose_service.ComposeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return ComposePublishOut(task=_to_out(task, product.name), asset_id=asset.id)
