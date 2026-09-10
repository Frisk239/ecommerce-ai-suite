"""顾客通道路由（ADR 0021/0033）：不登录、会话级身份、同一引擎。

- ``POST /api/customer/sessions``：签发会话令牌（secrets.token_urlsafe(32) 存
  原文，迁移 0005 落列）；201 返回 ``{session_id, token}``。无操作者鉴权
  （0021：顾客不是本套件账号），靠 IP 建会话限流（0033：不做无令牌狂刷）。
- ``POST /api/customer/sessions/{id}/messages``：``Authorization: Bearer <token>``
  鉴权（统一走 `_authorize_customer_session`：会话存在 + compare_digest 恒定时间
  比对 + **未过期**；会话不存在/令牌无效/**令牌过期** 统一 401 带
  WWW-Authenticate——自增 id 不可探测，过期也不单独提示），
  发问走 chat_engine（0021 同一引擎：与操作者预览同事件序 thinking ->
  delta* -> complete），**complete 不带 gap_id**（spec 工程裁决：顾客不暴露
  内部缺口 id，事件载荷白名单裁剪——拒答照常落缺口，操作者在治理台可见）。
- ``POST /api/customer/sessions/{id}/messages/{mid}/feedback``（第 40 刀，
  ADR 0044 §四）：顾客 thumbs-down「没有帮助」——闸序与鉴权同发问；body
  白名单只有 helpful（v1 拒绝 true，thumbs-up 是 Out）；仅 kind=answer 且
  citations 非空的消息可反馈（拒答/转人工无按钮也不收反馈），幂等=已反馈
  409；分诊在代码：逐 citation 资产 last_verified_at=NULL（撤销验证，复审
  由治理台未验证面自然承接）。
- ``POST /api/customer/sessions/{id}/handoff-tickets/{tid}``（第 42 刀，
  ADR 0046 §4）：转人工工单留联系方式——闸序同上；name/note 必填、email/
  phone 可选轻校验；工单须属于本会话（统一 404）；顾客回显自己的输入不掩。

顾客没有 GET/列表/回流端点（0021：回流登记仍是操作者动作，顾客接口不能
发布；拉历史属本刀 Out）。限流三道闸见 services/rate_limit（ADR 0033）。
"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from suite_api.deps import get_db
from suite_api.models import Asset, HandoffTicket, ServiceMessage, ServiceSession
from suite_api.services.chat_engine import run_ask, sse_event_stream
from suite_api.services.handoff_tickets import submit_contact, ticket_no
from suite_api.services.rate_limit import CustomerRateLimits

router = APIRouter(prefix="/api/customer", tags=["customer"])

# 联系方式轻校验（第 42 刀，ADR 0046 §4）：邮箱须有 @ 与域名点，电话只收
# 数字/常见分隔符——不做严格 RFC 校验（联系方式是回访线索不是身份凭证），
# 但要挡住明显乱填；号码未被 redact 规则覆盖的（带分隔符）也允许落库。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[0-9+\-() ]{5,20}$")


def triage_asset_ids(citations: list[dict[str, Any]]) -> list[int]:
    """负反馈分诊的资产集合（纯函数便于单测）：逐 citation 收集 asset_id
    去重升序——分诊语义见 leave_feedback（ADR 0044 §四）。

    防御式解析（第 43 刀加固，读路径复用后不允许坏行 500）：只收 dict 且
    ``asset_id`` 为 int 的条目——键缺失、``asset_id: null``、字符串 id 都跳过
    （``int(None)`` 会抛 TypeError；bool 是 int 子类，防御性排除，同 lineage 口径）。
    """
    asset_ids: set[int] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        asset_id = citation.get("asset_id")
        if isinstance(asset_id, int) and not isinstance(asset_id, bool):
            asset_ids.add(asset_id)
    return sorted(asset_ids)

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


def _authorize_customer_session(db: Session, session_id: int, request: Request) -> ServiceSession:
    """顾客会话鉴权单一出处（第 45 刀）：存在 + Bearer 恒定时间比对 + **未过期**。

    三处（发问 / 反馈 / 提交联系方式）此前各抄一份同样的判断；第 45 刀把令牌 TTL
    加进来时若再抄第四份，迟早漂移。**过期与无效同 401 同文案**（见
    `_unauthorized`）：不向调用方区分「令牌错」与「令牌过期」——那既是对攻击者的
    信息泄露，也和既有口径一致（会话不存在与令牌无效本来就不区分）。

    `expires_at` 为 NULL 视为不可用（严格）：迁移 0021 已把存量顾客会话按
    `created_at + 24h` 回填，NULL 出现即为异常，不给静默放行的口子。
    """
    session = db.get(ServiceSession, session_id)
    token = _bearer_token(request)
    if (
        session is None
        or token is None
        or session.customer_token is None
        or not compare_digest(session.customer_token.encode(), token.encode())
    ):
        raise _unauthorized()
    expires_at = session.customer_token_expires_at
    if expires_at is None or datetime.now(UTC) > expires_at:
        raise _unauthorized()
    return session


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

    令牌只在本响应里完整出现一次，顾客侧自行保存；**签发即带 TTL**（第 45 刀：
    `customer_token_ttl_seconds`，默认 24h）——过期后与无效同 401 同文案。不做
    刷新/续期/吊销（本刀 Out）；会话终结（操作者回流登记置 registered）后令牌
    随之失去发问资格。
    """
    retry_after = limits.check_create(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    ttl_seconds = request.app.state.settings.customer_token_ttl_seconds
    session = ServiceSession(
        status=ACTIVE,
        customer_token=token,
        customer_token_expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
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

    session = _authorize_customer_session(db, session_id, request)

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

    # expose_gap_id=False：顾客白名单一个闸管两处（第 27 刀起延伸到拒答消息
    # 文本——不带「缺口：G-xxxx」段；complete 载荷不带 gap_id 口径不变）
    outcome = await run_ask(db, session, question, expose_gap_id=False)
    # SSE 生成器不碰 DB：返回前归还连接，慢客户端不再钉住池（get_db 幂等 close）
    db.commit()
    db.close()
    return StreamingResponse(
        sse_event_stream(outcome, expose_gap_id=False), media_type="text/event-stream"
    )


# ---------- 反馈（第 40 刀，ADR 0044 §四：thumbs-down 分诊） ----------


class FeedbackBody(BaseModel):
    helpful: bool


class FeedbackOut(BaseModel):
    message_id: int
    feedback: dict[str, str | bool]
    # 分诊即答案：本次负反馈撤销验证的资产（前端可提示「已反馈，资料进入复审」）
    triaged_asset_ids: list[int]


@router.post("/sessions/{session_id}/messages/{message_id}/feedback")
def leave_feedback(
    session_id: int,
    message_id: int,
    body: FeedbackBody,
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
    limits: Annotated[CustomerRateLimits, Depends(get_rate_limits)] = None,
) -> FeedbackOut:
    """顾客 thumbs-down「没有帮助」（ADR 0044 §四）。

    闸序与鉴权同发问：IP 闸（先于鉴权省 DB）-> 401（会话不存在与令牌无效
    统一文案）-> 409（非 active：会话终结后反馈面一并收口）。仅 kind=answer
    且 citations 非空的消息可反馈（拒答/转人工无按钮也不收——404/409）；幂等
    =已反馈 409；body 白名单只有 helpful，v1 拒绝 true（thumbs-up 是 Out）。
    分诊（在代码）：逐 citation 资产 last_verified_at=None——「发布=验证快照」
    被负反馈推翻，复审由治理台未验证/stale 面自然承接；commit 后返回分诊
    资产列表（分诊即答案）。"""
    retry_after = limits.check_ask_ip(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    session = _authorize_customer_session(db, session_id, request)
    if session.status != ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以反馈，当前状态: {session.status}",
        )
    if body.helpful is not False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="本版本只接受「没有帮助」反馈",
        )

    message = db.get(ServiceMessage, message_id)
    if message is None or message.session_id != session_id or message.role != "agent":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
    if message.kind != "answer" or not message.citations:
        # 拒答/转人工/无引用的消息没有「没有帮助」入口（ADR 0044：citations
        # 空则分诊无从谈起）
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该消息不接受反馈")
    if message.feedback is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该消息已反馈过")

    triaged = triage_asset_ids(message.citations)
    for asset_id in triaged:
        asset = db.get(Asset, asset_id)
        if asset is not None:
            asset.last_verified_at = None
    message.feedback = {"helpful": False, "at": datetime.now(UTC).isoformat()}
    db.commit()
    db.refresh(message)
    return FeedbackOut(
        message_id=message.id,
        feedback=dict(message.feedback),
        triaged_asset_ids=triaged,
    )


# ---------- 转人工联系方式（第 42 刀，ADR 0046 §4） ----------


class HandoffContactBody(BaseModel):
    """联系方式表单：name+note 必填、email/phone 可选、整表可跳过（不填也能拿到
    工单——强制留联系方式伤转化；联系方式只是让工单可回访）。"""

    name: str
    note: str
    email: str | None = None
    phone: str | None = None


class HandoffContactAck(BaseModel):
    """顾客提交联系方式的回执（ADR 0046 §4）：只回工单号与提交时间。

    顾客面回显自己的输入不掩，也不需要操作者的掩码出口模型——本模型独立，
    不跨 route 复用 HandoffTicketOut（顾客 ack ≠ 操作者视图）。
    """

    ticket_no: str
    contact_at: datetime


@router.post("/sessions/{session_id}/handoff-tickets/{ticket_id}")
def submit_handoff_contact(
    session_id: int,
    ticket_id: int,
    body: HandoffContactBody,
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
    limits: Annotated[CustomerRateLimits, Depends(get_rate_limits)] = None,
) -> HandoffContactAck:
    """顾客提交本会话工单的联系方式（ADR 0046 §4）。

    闸序与发问/反馈一致：IP 闸（先于鉴权省 DB）-> 401（会话不存在与令牌无效
    统一文案）-> 会话闸 -> 409（非 active：会话终结后联系方式面一并收口）-> 404
    （工单不存在或不属于本会话——统一 404，不泄露其他会话工单存在性）-> 422
    （name/note 非空、email/phone 轻校验）-> 落库。返回工单号+提交时间的小回执
    （顾客面不需要操作者视图；原文只落库，操作者面出口掩）。
    """
    retry_after = limits.check_ask_ip(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    session = _authorize_customer_session(db, session_id, request)

    retry_after = limits.check_ask_session(str(session_id))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    if session.status != ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以提交联系方式，当前状态: {session.status}",
        )

    ticket = db.get(HandoffTicket, ticket_id)
    if ticket is None or ticket.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")

    name = body.name.strip()
    note = body.note.strip()
    if not name or not note:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="姓名与留言不能为空"
        )
    email = (body.email or "").strip() or None
    phone = (body.phone or "").strip() or None
    if email is not None and _EMAIL_RE.fullmatch(email) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="邮箱格式不正确"
        )
    if phone is not None and _PHONE_RE.fullmatch(phone) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="电话格式不正确"
        )

    submit_contact(db, ticket, name=name, note=note, email=email, phone=phone)
    # 联系人原文只落库；顾客 ack 只回工单号+提交时间（不借操作者掩码模型）
    return HandoffContactAck(ticket_no=ticket_no(ticket), contact_at=ticket.contact_at)
