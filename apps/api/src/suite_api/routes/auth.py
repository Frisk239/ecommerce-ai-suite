"""认证（0016 最小版）：单操作者登录/登出/当前身份，签名 httpOnly cookie。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, request_settings
from suite_api.models import Operator
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


@router.post("/login", response_model=OperatorOut)
def login(
    body: LoginBody,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(request_settings)],
) -> OperatorOut:
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
