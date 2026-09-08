"""顾客通道路由（ADR 0021/0033）：不登录、会话级身份、同一引擎。

- ``POST /api/customer/sessions``：签发会话令牌（secrets.token_urlsafe(32) 存
  原文，迁移 0005 落列）；201 返回 ``{session_id, token}``。无操作者鉴权
  （0021：顾客不是本套件账号），靠 IP 建会话限流（0033：不做无令牌狂刷）。
- ``POST /api/customer/sessions/{id}/messages``：``Authorization: Bearer <token>``
  鉴权（compare_digest 恒定时间比较；会话不存在与令牌无效统一 401 带
  WWW-Authenticate——自增 id 不可探测），
  发问走 chat_engine（0021 同一引擎：与操作者预览同事件序 thinking ->
  delta* -> complete），**complete 不带 gap_id**（spec 工程裁决：顾客不暴露
  内部缺口 id，事件载荷白名单裁剪——拒答照常落缺口，操作者在治理台可见）。

顾客没有 GET/列表/回流端点（0021：回流登记仍是操作者动作，顾客接口不能
发布；拉历史属本刀 Out）。限流三道闸见 services/rate_limit（ADR 0033）。
"""

import secrets
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from suite_api.deps import get_db
from suite_api.models import ServiceSession
from suite_api.services.chat_engine import run_ask, sse_event_stream
from suite_api.services.rate_limit import CustomerRateLimits

router = APIRouter(prefix="/api/customer", tags=["customer"])

ACTIVE = "active"

# 令牌熵（字节）：token_urlsafe(32) ≈ 256 bit，输出 43 字符，String(64) 容得下
_TOKEN_BYTES = 32


class CustomerSessionCreated(BaseModel):
    session_id: int
    token: str


class AskBody(BaseModel):
    content: str


def client_ip(request: Request) -> str:
    """限流 IP 口径（两模式，settings.customer_trust_proxy 切换）。

    直连模式（默认，fail-closed）：只信 TCP 对端地址（request.client.host），
    完全忽略 X-Forwarded-For——该头是客户端自报的，直连部署下伪造它就能换 IP
    闸 key，等于没有 IP 托底。反代模式：信 XFF 第一跳，部署者负责让反向代理
    强制覆盖该头（README 顾客通道节写明两模式语义）。
    """
    forwarded = (
        request.headers.get("x-forwarded-for")
        if request.app.state.settings.customer_trust_proxy
        else None
    )
    first_hop = forwarded.split(",", 1)[0].strip() if forwarded else ""
    # 首跳空白（如「 , 10.0.0.1」的畸形头）不落空串 key：空串会让所有畸形
    # 请求共享同一本账、脱离真实对端口径（评审处置 2026-09-08）
    return first_hop or (request.client.host if request.client else "unknown")


def get_rate_limits(request: Request) -> CustomerRateLimits:
    """三道闸挂在 app.state（create_app 装配；测试可替换为小阈值/假时钟实例）。"""
    return request.app.state.customer_rate_limits


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None or not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def _unauthorized() -> HTTPException:
    # 会话不存在与令牌无效共用同一文案：自增 session id 配 404/401 双态等于
    # 存在性探测面，统一后不可区分（保留 WWW-Authenticate 满足 Bearer 语义）。
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="会话不存在或令牌无效",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"请求过于频繁，请约 {retry_after} 秒后再试",
        headers={"Retry-After": str(retry_after)},
    )


@router.post(
    "/sessions", response_model=CustomerSessionCreated, status_code=status.HTTP_201_CREATED
)
def create_session(
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
    limits: Annotated[CustomerRateLimits, Depends(get_rate_limits)] = None,
) -> CustomerSessionCreated:
    """签发顾客会话：新令牌随会话落库（非空即顾客会话，列表 origin=customer）。

    令牌只在本响应里完整出现一次，顾客侧自行保存；不做过期/刷新/吊销（本刀
    Out）——会话终结（操作者回流登记置 registered）后令牌随之失去发问资格。
    """
    retry_after = limits.check_create(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    session = ServiceSession(status=ACTIVE, customer_token=token)
    db.add(session)
    db.commit()
    db.refresh(session)
    return CustomerSessionCreated(session_id=session.id, token=token)


@router.post("/sessions/{session_id}/messages")
async def ask(
    session_id: int,
    body: AskBody,
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
    limits: Annotated[CustomerRateLimits, Depends(get_rate_limits)] = None,
) -> StreamingResponse:
    """顾客发问 -> SSE 流式回答（与操作者版同事件序；complete 不带 gap_id）。

    闸序（取舍见 services/rate_limit）：IP 闸（先于鉴权省 DB——狂刷无论令牌
    对错都不碰库）-> 401（会话不存在与令牌无效统一文案，自增 id 不可探测）
    -> 会话闸（后于鉴权保配额——无效令牌查得到会话但耗不了它的发问配额）
    -> 409（非 active）-> 422（空问句）-> 引擎。引擎主体与取舍见
    services/chat_engine（0021：两条通道行为只差鉴权与载荷白名单）。
    """
    retry_after = limits.check_ask_ip(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    session = db.get(ServiceSession, session_id)
    token = _bearer_token(request)
    if (
        session is None
        or token is None
        or session.customer_token is None
        or not compare_digest(session.customer_token.encode(), token.encode())
    ):
        raise _unauthorized()

    retry_after = limits.check_ask_session(str(session_id))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    if session.status != ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以继续发问，当前状态: {session.status}",
        )
    question = body.content.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息内容不能为空"
        )

    outcome = await run_ask(db, session, question)
    # SSE 生成器不碰 DB：返回前归还连接，慢客户端不再钉住池（get_db 幂等 close）
    db.commit()
    db.close()
    return StreamingResponse(
        sse_event_stream(outcome, expose_gap_id=False), media_type="text/event-stream"
    )
