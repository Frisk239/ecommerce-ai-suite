"""运营 Agent 路由（第 22 刀，ADR 0041）：编排任务的建/查/失败续跑/投放确认。

四端点全操作者 cookie 鉴权（编排是操作者动作，0016 控制台=登录后的人机界面）；
路由恒为**同步 def**——建任务与重试在请求内同步执行三步（含 `asyncio.run
(llm.complete_chat)` ≤20s，前提同素材生成/考核打分，第 16 刀 P1#1）。

状态机与三步执行在 services/ops.py；本层只做入参校验、404 闸门与视图装配。
run 不是中台对象（0041）：这里没有资产三态语义，refs 只是「compose 时刻冻结
的已发布版本」引用锚（链治理台详情）；「投放发布」是渠道动作（记确认时间），
不改变任何资产状态。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db
from suite_api.models import Operator, OpsRun, Product
from suite_api.services.asset_view import load_product_names
from suite_api.services.ops import deliver_run, retry_run, start_run

router = APIRouter(prefix="/api/ops", tags=["ops"])


class OpsRunIn(BaseModel):
    product_id: int


class OpsStepOut(BaseModel):
    key: str
    name: str
    via: str
    status: str  # pending | running | done | failed
    detail: str


class OpsRefOut(BaseModel):
    asset_id: int
    version_no: int


class OpsOutputOut(BaseModel):
    title: str
    body: str
    refs: list[OpsRefOut]


class OpsRunOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    steps: list[OpsStepOut]
    output: OpsOutputOut | None
    delivered_at: datetime | None
    created_at: datetime


def _run_or_404(db: Session, run_id: int) -> OpsRun:
    run = db.get(OpsRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="编排任务不存在")
    return run


def _product_name(db: Session, product_id: int) -> str:
    """product_id 有 FK 保证行存在；视图仍防漂（缺名回退占位，不 500）。"""
    product = db.get(Product, product_id)
    return product.name if product is not None else "—"


def _to_out(db: Session, run: OpsRun, names: dict[int, str] | None = None) -> OpsRunOut:
    """run→视图；names 传入则为列表批取的商品名映射（第 26 刀 P1④：
    22 刀列表逐行 db.get(Product) 的 N+1 回归，收口回 asset_view.load_product_names
    批取——debt-2 第 24 刀同款）。单行端点不传，走 _product_name 防回退。"""
    steps: list[OpsStepOut] = [OpsStepOut(**step) for step in dict_list(run.steps)]
    output = OpsOutputOut(**dict(run.output)) if run.output is not None else None
    if names is None:
        product_name = _product_name(db, run.product_id)
    else:
        product_name = names.get(run.product_id) or "—"
    return OpsRunOut(
        id=run.id,
        product_id=run.product_id,
        product_name=product_name,
        steps=steps,
        output=output,
        delivered_at=run.delivered_at,
        created_at=run.created_at,
    )


def dict_list(value: Any) -> list[dict[str, Any]]:
    """JSONB 回读防御：非 list/元素非 dict 时按空轨迹处理（坏行不 500）。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


@router.post("/runs", response_model=OpsRunOut, status_code=status.HTTP_201_CREATED)
def create_run(
    body: OpsRunIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> OpsRunOut:
    """选商品开始编排：**同步就地执行**三步（读商品→生成文案→组装），
    返回时已是稳定态（三步 done 或断在失败步），running 只是请求内瞬时态。"""
    del operator  # 写接口仅要求登录，401 口径同既有写端点
    run = start_run(db, body.product_id)
    return _to_out(db, run)


@router.get("/runs", response_model=list[OpsRunOut])
def list_runs(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[OpsRunOut]:
    """编排轨迹列表（id 倒序，最新在前；run 不进检索、无分页——Out 口径）。

    第 26 刀 P1④（audit-5 技术债④）：列表批取商品名（load_product_names），
    消 22 刀逐行 db.get(Product) 的 N+1 回归——单查询形状同 material/clips
    列表（debt-2 第 24 刀先例）。"""
    del operator  # 读接口同样要求登录
    runs = list(db.scalars(select(OpsRun).order_by(OpsRun.id.desc())))
    names = load_product_names(db, {run.product_id for run in runs})
    return [_to_out(db, run, names) for run in runs]


@router.post("/runs/{run_id}/retry", response_model=OpsRunOut)
def retry(
    run_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> OpsRunOut:
    """失败续跑：failed 步复位后从失败步起执行，前序 done 不重跑；无失败步 409。"""
    del operator
    return _to_out(db, retry_run(db, run_id))


@router.post("/runs/{run_id}/deliver", response_model=OpsRunOut)
def deliver(
    run_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> OpsRunOut:
    """投放确认（渠道动作 mock）：三步全 done 才记 delivered_at；已投放/未完成 409。
    不改变任何资产的三态——治理台的「发布」是另一件事（词条「发布」_Avoid_ 钉死）。"""
    del operator
    return _to_out(db, deliver_run(db, run_id))
