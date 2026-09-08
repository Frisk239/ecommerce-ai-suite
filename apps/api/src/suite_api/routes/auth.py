"""认证（0016 最小版）：单操作者登录/登出/当前身份，签名 httpOnly cookie。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, request_settings
from suite_api.models import Operator
from suite_api.services.rate_limit import SlidingWindowLimiter
from suite_api.services.seed import check_password
from suite_api.services.sessions import SESSION_COOKIE_NAME, issue_session_value
from suite_api.settings import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


class OperatorOut(BaseModel):
    id: int
    username: str


def _set_session_cookie(response: Response, operator_id: int, settings: Settings) -> None:
    value = issue_session_value(operator_id, settings.session_secret, settings.session_ttl_seconds)
    # httpOnly 防 JS 读取；SameSite=Lax 防跨站携带；开发走 http 故不设 Secure（残留风险见 README）
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
    )


def _login_ip(request: Request) -> str:
    """登录闸只信 TCP 对端（操作者控制台，不复用顾客 XFF/CUSTOMER_TRUST_PROXY）。"""
    return request.client.host if request.client else "unknown"


def _rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"请求过于频繁，请约 {retry_after} 秒后再试",
        headers={"Retry-After": str(retry_after)},
    )


@router.post("/login", response_model=OperatorOut)
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(request_settings)],
) -> OperatorOut:
    limiter: SlidingWindowLimiter = request.app.state.login_limiter
    # 成功也计（防爆破优先）；闸在验密之前，狂刷不把密码校验当配额外通道
    retry_after = limiter.check(_login_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)
    operator = db.scalar(select(Operator).where(Operator.username == body.username))
    # 统一文案，不区分「用户不存在」与「密码错误」
    if operator is None or not check_password(body.password, operator.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    _set_session_cookie(response, operator.id, settings)
    return OperatorOut(id=operator.id, username=operator.username)


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"detail": "已退出登录"}


@router.get("/me", response_model=OperatorOut)
def me(operator: Annotated[Operator, Depends(get_current_operator)]) -> OperatorOut:
    return OperatorOut(id=operator.id, username=operator.username)
