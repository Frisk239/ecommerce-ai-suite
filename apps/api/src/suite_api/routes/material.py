"""素材中心路由（第 17 刀，ADR 0038；第 98 刀内容套件，ADR 0055）：卖点文案
任务的建（三模板+可选配图）/查/抽检通过（双资产登记）/打回/重试。

全部操作者 cookie 鉴权（素材是操作者动作，0016 控制台=登录后的人机界面）；
路由恒为**同步 def**（FastAPI 线程池）——建任务与重试请求内同步执行生成+
双闸质检+配图（`asyncio.run(llm.complete_chat)` ≤20s ×2 + 文生图 ≤60s，
线程上无运行中事件循环，前提同 machine_wash QA 抽取，第 16 刀 P1#1 的按
loop 缓存客户端也依赖这一点）。

状态机与质检双闸在 services/material.py；本层只做入参校验、404 闸门与视图
装配。非法转移由服务层抛 409（如 approve 非 pending_qc）。任务不是中台对象
（0012）：这里没有资产三态的语义，registered 只是「已登记出资产」的回执锚
（asset_id/image_asset_id 供 UI 跳治理台详情）。

第 98 刀三个新面：
- ``POST /tasks`` 收 ``template``（三选一，默认站内）与 ``with_image``
  （无 IMGGEN key 不 409——配图步诚实跳过，任务详情如实标注，ADR 0055）；
- ``GET /imggen/status``：文生图配置状态（前端「生成配图」开关禁用判据，
  同 clips 的 asr/status 先例——后端仍是唯一闸）；
- ``GET /tasks/{id}/image``：抽检前的配图**暂存字节**预览（操作者面；同版本
  正文端点的先例——媒体端点只出已发布，这里出的是任务暂存件不是资产）。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import MaterialTask, Operator, Product
from suite_api.services import imggen as imggen_service
from suite_api.services.asset_view import load_product_names
from suite_api.services.material import (
    DEFAULT_TEMPLATE,
    IMAGE_NONE,
    IMAGE_PENDING,
    IMAGE_REQUESTED,
    QUEUED,
    REJECT_REASON_MAX,
    TEMPLATES,
    approve_task,
    reject_task,
    retry_image_step,
    retry_task,
    run_generation_task,
    validate_template,
)
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/material", tags=["material"])


class MaterialTaskIn(BaseModel):
    product_id: int
    # 内容模板（第 98 刀，ADR 0055）：station=站内投放文案（默认，17 刀形态）/
    # xhs=小红书笔记体 / short_video=短视频口播稿——prompt 模板参数，非 Agent。
    template: str = DEFAULT_TEMPLATE
    # 请求配图（文生图）。无 IMGGEN_API_KEY 时**不 409**：配图步诚实跳过
    # （image_status=skipped_no_key，任务详情标注）——配图是增值项不是任务本体。
    with_image: bool = False


class MaterialTaskOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    status: str
    title: str | None
    content: str | None
    last_error: str | None
    asset_id: int | None
    template: str
    template_name: str
    qc_llm_passed: bool | None  # None=未跑到（两闸独立记录，ADR 0055）
    image_status: str
    image_asset_id: int | None
    # 第 115 刀（W15a）：美化产品图所基于的真实图片资产（血缘锚，UI 展示
    # 「基于 A-xxxx 美化生成」）；文字卡/文生图背景/跳过态为 None
    image_reference_asset_id: int | None
    created_at: datetime


class ImggenStatusOut(BaseModel):
    configured: bool


def _task_or_404(db: Session, task_id: int) -> MaterialTask:
    task = db.get(MaterialTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材任务不存在")
    return task


def _product_name(db: Session, product_id: int) -> str:
    """product_id 有 FK 保证行存在；视图仍防漂（缺名回退占位，不 500）。"""
    product = db.get(Product, product_id)
    return product.name if product is not None else "—"


def _to_out(task: MaterialTask, product_name: str) -> MaterialTaskOut:
    template = task.template or DEFAULT_TEMPLATE
    return MaterialTaskOut(
        id=task.id,
        product_id=task.product_id,
        product_name=product_name,
        status=task.status,
        title=task.title,
        content=task.content,
        last_error=task.last_error,
        asset_id=task.asset_id,
        template=template,
        template_name=TEMPLATES[template]["name"],
        qc_llm_passed=task.qc_llm_passed,
        image_status=task.image_status or IMAGE_NONE,
        image_asset_id=task.image_asset_id,
        image_reference_asset_id=task.image_reference_asset_id,
        created_at=task.created_at,
    )


@router.get("/imggen/status", response_model=ImggenStatusOut)
def imggen_status(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
) -> ImggenStatusOut:
    """文生图配置状态（第 98 刀）：前端「生成配图」开关禁用判据。

    只回布尔（配置细节与密钥都不进前端）；后端仍是唯一闸——前端被绕过时，
    配图步对无 key 环境也只是诚实跳过（fail-closed 不 fail 任务，ADR 0055）。
    """
    del operator  # 读接口同样要求登录
    return ImggenStatusOut(configured=imggen_service.is_configured())


@router.post("/tasks", response_model=MaterialTaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: MaterialTaskIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """建任务并**同步就地执行**生成+双闸质检+配图（0038/0055）：返回时已是
    稳定态（pending_qc 或 failed），queued/running 只是请求内的瞬时态。"""
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    try:
        validate_template(body.template)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    task = MaterialTask(
        product_id=product.id,
        status=QUEUED,
        template=body.template,
        # requested 兼作 with_image 请求标志（迁移 0032）：配图步按非 none 跑
        image_status=IMAGE_REQUESTED if body.with_image else IMAGE_NONE,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    # 配图步会暂存字节（material/ 前缀），生成侧自此需要 storage（98 刀起）
    run_generation_task(db, storage, task)
    return _to_out(task, _product_name(db, task.product_id))


@router.get("/tasks", response_model=list[MaterialTaskOut])
def list_tasks(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[MaterialTaskOut]:
    del operator  # 读接口同样要求登录
    tasks = list(db.scalars(select(MaterialTask).order_by(MaterialTask.id.desc())))
    # 批取商品名（debt-2 N+1）：一次 IN 查询替代逐行 db.get，先例 asset_view.load_products
    names = load_product_names(db, {t.product_id for t in tasks})
    return [_to_out(t, names.get(t.product_id, "—")) for t in tasks]


@router.get("/tasks/{task_id}", response_model=MaterialTaskOut)
def get_task(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> MaterialTaskOut:
    del operator
    task = _task_or_404(db, task_id)
    return _to_out(task, _product_name(db, task.product_id))


@router.get("/tasks/{task_id}/image")
def get_task_image(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> Response:
    """配图暂存字节预览（第 98 刀，**操作者面**）：抽检前看得到配图长什么样。

    服务的是任务暂存件（material/ 前缀，不是资产）——媒体端点（94b）只出
    已发布，这里同「版本正文端点」的先例：操作者治理语境的预览通道。配图
    登记后（image_status=registered）此端点 404：预览去治理台资产详情看。
    404 同文案不暴露对象键（同媒体端点口径）。
    """
    del operator
    task = _task_or_404(db, task_id)
    if task.image_status != IMAGE_PENDING or not task.image_object_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该任务没有待抽检的配图")
    try:
        data = storage.get_bytes(task.image_object_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="该任务没有待抽检的配图"
        ) from exc
    from suite_api.services.vlm import image_mime  # 延迟导入：单一魔数真源

    return Response(content=data, media_type=image_mime(data) or "application/octet-stream")


@router.post("/tasks/{task_id}/approve", response_model=MaterialTaskOut)
def approve(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """抽检通过：文案登记为素材资产 + 配图登记为独立图片资产（第 98 刀双资产，
    回执 asset_id/image_asset_id；配图未生成/跳过=单资产，0029/0038/0055）；
    任务 registered（终态）。"""
    del operator
    task = _task_or_404(db, task_id)
    approve_task(db, storage, task)  # 抽检通过才碰字节：register_asset 写对象存储
    return _to_out(task, _product_name(db, task.product_id))


class RejectBody(BaseModel):
    """打回请求体（第 48 刀）：reason 可选——不传 / null / 空白 = 无理由。"""

    reason: str | None = None


@router.post("/tasks/{task_id}/reject", response_model=MaterialTaskOut)
def reject(
    task_id: int,
    body: RejectBody | None = None,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> MaterialTaskOut:
    """抽检打回：任务 failed，不登记任何字节（0029）；可重试。

    第 48 刀：收可选打回理由（≤200 字）写进 last_error 的详情段（``人工打回：…``）；
    超长 422、不传等价无理由。body 允许缺省（老前端不带 body 仍能打回——兼容
    优先于强制填理由）。commit 在服务层 reject_task（debt-2 层次收口）。
    """
    del operator
    reason = (body.reason if body is not None else None) or ""
    reason = reason.strip()
    if len(reason) > REJECT_REASON_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"打回理由不能超过 {REJECT_REASON_MAX} 字",
        )
    task = _task_or_404(db, task_id)
    reject_task(db, task, reason=reason)
    return _to_out(task, _product_name(db, task.product_id))


@router.post("/tasks/{task_id}/retry", response_model=MaterialTaskOut)
def retry(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """失败重试：同任务行新一次生成（failed → running → 稳定态；不绕 queued，
    清 last_error 在 run_generation_task 内，debt-2 死转移收口）。配图步按
    image_status 非 none 重跑（requested 标志建任务时落，重试不丢请求）。"""
    del operator
    task = _task_or_404(db, task_id)
    retry_task(db, storage, task)  # 配图步会重写暂存字节，重试也需要 storage
    return _to_out(task, _product_name(db, task.product_id))


@router.post("/tasks/{task_id}/retry-image", response_model=MaterialTaskOut)
def retry_image(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """补配图（第 115 刀 W15a）：待抽检任务的配图没成/没出时**只重跑配图步**。

    文案已过双闸不动（要重写文案走打回→重试）；适用 image_status ∈
    requested/skipped_no_key/skipped_no_image/failed——「先给商品传图、
    再回来补配图」的闭环出口。站内模板配图=美化产品图（基于真实商品图编辑），
    补跑前先满足前置：商品挂图片资产 + IMGGEN key。"""
    del operator
    task = _task_or_404(db, task_id)
    retry_image_step(db, storage, task)
    return _to_out(task, _product_name(db, task.product_id))
