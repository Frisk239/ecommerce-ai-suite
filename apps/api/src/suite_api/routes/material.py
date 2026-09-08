"""素材中心路由（第 17 刀，ADR 0038）：卖点文案任务的建/查/抽检通过/打回/重试。

六端点全部操作者 cookie 鉴权（素材是操作者动作，0016 控制台=登录后的人机
界面）；路由恒为**同步 def**（FastAPI 线程池）——建任务与重试请求内同步执行
生成（`asyncio.run(llm.complete_chat)` ≤20s，线程上无运行中事件循环，前提同
machine_wash QA 抽取，第 16 刀 P1#1 的按 loop 缓存客户端也依赖这一点）。

状态机与规则质检在 services/material.py；本层只做入参校验、404 闸门与视图
装配。非法转移由服务层抛 409（如 approve 非 pending_qc）。任务不是中台对象
（0012）：这里没有资产三态的语义，registered 只是「已登记出资产」的回执锚
（asset_id 供 UI 跳治理台详情）。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import MaterialTask, Operator, Product
from suite_api.services.material import (
    QUEUED,
    approve_task,
    reject_task,
    retry_task,
    run_generation_task,
)
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/material", tags=["material"])


class MaterialTaskIn(BaseModel):
    product_id: int


class MaterialTaskOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    status: str
    title: str | None
    content: str | None
    last_error: str | None
    asset_id: int | None
    created_at: datetime


def _task_or_404(db: Session, task_id: int) -> MaterialTask:
    task = db.get(MaterialTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材任务不存在")
    return task


def _product_name(db: Session, product_id: int) -> str:
    """product_id 有 FK 保证行存在；视图仍防漂（缺名回退占位，不 500）。"""
    product = db.get(Product, product_id)
    return product.name if product is not None else "—"


def _to_out(db: Session, task: MaterialTask) -> MaterialTaskOut:
    return MaterialTaskOut(
        id=task.id,
        product_id=task.product_id,
        product_name=_product_name(db, task.product_id),
        status=task.status,
        title=task.title,
        content=task.content,
        last_error=task.last_error,
        asset_id=task.asset_id,
        created_at=task.created_at,
    )


@router.post("/tasks", response_model=MaterialTaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: MaterialTaskIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """建任务并**同步就地执行**生成+规则质检（0038）：返回时已是稳定态
    （pending_qc 或 failed），queued/running 只是请求内的瞬时态。"""
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    task = MaterialTask(product_id=product.id, status=QUEUED)
    db.add(task)
    db.commit()
    db.refresh(task)
    run_generation_task(db, storage, task)
    return _to_out(db, task)


@router.get("/tasks", response_model=list[MaterialTaskOut])
def list_tasks(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[MaterialTaskOut]:
    del operator  # 读接口同样要求登录
    tasks = list(db.scalars(select(MaterialTask).order_by(MaterialTask.id.desc())))
    return [_to_out(db, t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=MaterialTaskOut)
def get_task(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> MaterialTaskOut:
    del operator
    return _to_out(db, _task_or_404(db, task_id))


@router.post("/tasks/{task_id}/approve", response_model=MaterialTaskOut)
def approve(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """抽检通过：登记为素材资产（kind=material/来源=素材生成/已接入→机洗推进
    待人洗，0029/0038）；任务 registered（终态）。"""
    del operator
    task = _task_or_404(db, task_id)
    approve_task(db, storage, task)
    return _to_out(db, task)


@router.post("/tasks/{task_id}/reject", response_model=MaterialTaskOut)
def reject(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> MaterialTaskOut:
    """抽检打回：任务 failed（原因=人工打回），不登记任何字节（0029）；可重试。"""
    del operator
    task = _task_or_404(db, task_id)
    reject_task(task)
    db.commit()
    return _to_out(db, task)


@router.post("/tasks/{task_id}/retry", response_model=MaterialTaskOut)
def retry(
    task_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> MaterialTaskOut:
    """失败重试：同任务行新一次生成（清 last_error → running → 稳定态）。"""
    del operator
    task = _task_or_404(db, task_id)
    retry_task(db, storage, task)
    return _to_out(db, task)
