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
  ADR 0044 §四；第 48 刀放开正反馈）：顾客 thumbs——闸序与鉴权同发问；body
  只有 helpful 一个字段（缺字段 422）；仅 kind=answer 且 citations 非空的
  消息可反馈（拒答/转人工无按钮也不收反馈），幂等=已反馈 409；
  ``helpful=false`` 走分诊（逐 citation 资产 last_verified_at=NULL，撤销验证，
  复审由治理台未验证面自然承接），``helpful=true`` 只记档不分诊。
- ``POST /api/customer/sessions/{id}/handoff-tickets/{tid}``（第 42 刀，
  ADR 0046 §4）：转人工工单留联系方式——闸序同上；name/note 必填、email/
  phone 可选轻校验；工单须属于本会话（统一 404）；顾客回显自己的输入不掩。

- ``POST /api/customer/sessions/{id}/rating``（第 48 刀；第 71 刀评分可改）：会话级
  1–5 星 CSAT——闸序同上；score 越界 422、非 active 409、已评再提交是**改评**
  （UPDATE 覆盖式留最新，updated_at 记修改）；comment 可选（≤500 字，整体覆盖，
  不带即清空）。
低分不触发任何写动作（CSAT 是主观分，不是证据语义）。

- ``POST /api/customer/sessions/{id}/end``（第 80 刀）：顾客主动结束会话——
  active -> ended（幂等、不可逆），落 closed_at；ended 只关「发问」（409），
  评分/反馈/联系方式等善后通道照常放行；已回流登记（registered）的会话不能
  再结束（409）。操作者仍可把 ended 会话回流登记为 registered。

顾客没有 GET/列表/回流端点（0021：回流登记仍是操作者动作，顾客接口不能
发布；拉历史属本刀 Out）。限流三道闸见 services/rate_limit（ADR 0033）。
"""

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from suite_api.deps import get_db
from suite_api.models import (
    Asset,
    HandoffTicket,
    ServiceMessage,
    ServiceSession,
    SessionRating,
)
from suite_api.observability import record_chat_request, record_csat_rating
from suite_api.services.chat_engine import run_ask, sse_event_stream
from suite_api.services.handoff_tickets import submit_contact, ticket_no
from suite_api.services.rate_limit import CustomerRateLimits
from suite_api.services.triage import triage_asset_ids

router = APIRouter(prefix="/api/customer", tags=["customer"])

logger = logging.getLogger(__name__)

# 联系方式轻校验（第 42 刀，ADR 0046 §4）：邮箱须有 @ 与域名点，电话只收
# 数字/常见分隔符——不做严格 RFC 校验（联系方式是回访线索不是身份凭证），
# 但要挡住明显乱填；号码未被 redact 规则覆盖的（带分隔符）也允许落库。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[0-9+\-() ]{5,20}$")


ACTIVE = "active"
# 第 80 刀：顾客可主动结束会话（active -> ended 不可逆）；ended 仍可回流登记。
ENDED = "ended"
REGISTERED = "registered"

# 会话状态的中文映射（第 80 刀）：发问/结束两处闸把裸状态值翻给顾客看——
# 「ended」「registered」不是顾客能读懂的话。未收录值原样回显，不发明词。
_STATUS_LABELS = {ACTIVE: "进行中", ENDED: "已结束", REGISTERED: "已回流"}


def _status_label(status_value: str) -> str:
    return _STATUS_LABELS.get(status_value, status_value)


# 令牌熵（字节）：token_urlsafe(32) ≈ 256 bit，输出 43 字符，String(64) 容得下
_TOKEN_BYTES = 32


class CustomerSessionCreated(BaseModel):
    session_id: int
    token: str


class EndSessionOut(BaseModel):
    """结束会话回执（第 80 刀）：只回会话 id、终态与终结时刻。

    幂等重复调用返回同一 closed_at（首次结束时刻），故形态与首次一致。
    """

    id: int
    status: str
    closed_at: datetime | None


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


# ---------- 嵌入小组件的来源闸与访客 id（第 45b 刀）----------
_WIDGET_ORIGIN_HEADER = "X-Widget-Origin"
_WIDGET_VISITOR_HEADER = "X-Visitor-Id"
_VISITOR_ID_MAX = 64
# 宿主来源列长（models.ServiceSession.host_origin String(255)）
_HOST_ORIGIN_MAX = 255


def _widget_gate(request: Request) -> str | None:
    """嵌入来源闸：`WIDGET_ALLOWED_ORIGINS` 是嵌入的唯一闸，不在里面一律 403。

    只在请求**带了** `X-Widget-Origin` 时生效——那表示请求来自被嵌进宿主的页面
    （我们的 widget 页读 `document.referrer` 得出宿主来源后带上）；不带该头的是
    独立访问（`/customer` 直开），行为完全不变。白名单为空 = 未启用嵌入，同样 403。

    **为什么 `/customer` 被别的站 iframe 时也拦得住**：前端在**任何被框住的上下文**
    （`window.self !== window.top`，不只是 `/widget` 路由）都会带上这个头，因此
    第三方 iframe 我们的 `/customer` 一样会被这里 403；而宿主若用 no-referrer 剥掉
    来源，前端**不再静默退回独立访问**，而是直接拒绝建会话（fail-closed，见
    CustomerPage 的 framed 判定）。

    残留风险（如实记录，README 局限段同述）：该头由我们的前端填写，宿主若自己伪造
    仍可能过——要彻底堵死需在边缘/反代层拦文档请求（本仓是 dev 栈，没有这层）。
    """
    origin = (request.headers.get(_WIDGET_ORIGIN_HEADER) or "").strip().rstrip("/").lower()
    if origin == "":
        return None
    # 小写归一：浏览器报的 origin 主机名恒为小写，白名单手写成大写不该误拒（审计刀 8 P3）
    allowed = {
        item.strip().rstrip("/").lower()
        for item in request.app.state.settings.widget_allowed_origins.split(",")
        if item.strip()
    }
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="本站未启用嵌入客服（服务端未配置允许的来源）",
        )
    if origin not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"来源 {origin} 未获授权嵌入本站客服",
        )
    # 入库前截断到列长（host_origin String(255)）：与 _visitor_id 同口径——白名单
    # 被写成超长串时不该 500，截断即可（审计刀 11 P2）
    return origin[:_HOST_ORIGIN_MAX]


def _visitor_id(request: Request) -> str | None:
    """访客 id：宿主页第一方 uuid 的不透明传递，只做长度截断与空串归一。

    它是商家自己生成的标识（不承载我们的语义），故不做格式校验、不落任何用户
    输入原文；超长截断而不是报错（别让一个坏 id 把建会话挡死）。
    """
    raw = (request.headers.get(_WIDGET_VISITOR_HEADER) or "").strip()
    return raw[:_VISITOR_ID_MAX] if raw != "" else None


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

    嵌入来源闸（第 45b 刀）：**先于限流**——它只是一次 settings 读取（不碰库），
    未授权来源连配额都不该吃；独立访问（无该头）照旧走限流。
    """
    widget_origin = _widget_gate(request)
    retry_after = limits.check_create(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    ttl_seconds = request.app.state.settings.customer_token_ttl_seconds
    session = ServiceSession(
        status=ACTIVE,
        customer_token=token,
        customer_token_expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        # 访客 id 只在**嵌入请求**上收：独立访问自带这个头也不落（它不是商家的
        # 访客，落库就是脏数据——review P2）
        visitor_id=_visitor_id(request) if widget_origin is not None else None,
        # 宿主站点（第 54 刀）：过闸的来源归一值同落一行——商家挂多个站点时靠它
        # 分辨会话来自哪个站（独立访问没有宿主，为 NULL）
        host_origin=widget_origin,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return CustomerSessionCreated(session_id=session.id, token=token)


@router.post("/sessions/{session_id}/end", response_model=EndSessionOut)
def end_session(
    session_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
    limits: Annotated[CustomerRateLimits, Depends(get_rate_limits)] = None,
) -> EndSessionOut:
    """顾客结束会话（第 80 刀）：``active -> ended``，幂等且不可逆。

    闸序与评分端点完全同款：IP 闸（先于鉴权省 DB——狂刷无论令牌对错都不碰库）
    -> 401（会话不存在与令牌无效统一文案）-> 状态闸。无 body（结束不需要参数）。

    - ``active``：置 ``ended`` 并落 ``closed_at``；取 **DB 钟** ``func.now()``
      （第 71 刀教训：Python 钟与 DB 钟混用在 app/db 分机部署时会假翻转），
      commit 后 refresh 回读真值再返回。
    - ``ended``：幂等 200，**不重写 closed_at**（终结时刻以首次结束为准）。
    - ``registered``：409——已回流登记是更后的终态，不能反向结束。

    产品语义：结束=关闭对话流（发问），不关善后通道。故发问闸把 ended 挡在
    门外，而评分/反馈/联系方式闸放行 ended（见各闸判定与 `_status_label`）。
    """
    retry_after = limits.check_ask_ip(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    session = _authorize_customer_session(db, session_id, request)

    if session.status == ENDED:
        # 幂等：不重写 closed_at（终结时刻以首次结束为准）
        return EndSessionOut(id=session.id, status=session.status, closed_at=session.closed_at)
    if session.status != ACTIVE:
        # 只剩 registered（状态机无其他分支）：已回流不可再结束
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"会话状态：{_status_label(session.status)}，不能再结束",
        )

    session.status = ENDED
    session.closed_at = func.now()
    db.commit()
    db.refresh(session)
    logger.info("顾客结束会话: session=%s", session_id)
    return EndSessionOut(id=session.id, status=session.status, closed_at=session.closed_at)


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
        # 第 80 刀：状态值翻成顾客能读的中文（ended -> 已结束 / registered -> 已回流）
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以继续发问，当前状态: {_status_label(session.status)}",
        )
    question = body.content.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息内容不能为空"
        )

    # expose_gap_id=False：顾客白名单一个闸管两处（第 27 刀起延伸到拒答消息
    # 文本——不带「缺口：G-xxxx」段；complete 载荷不带 gap_id 口径不变）
    outcome = await run_ask(db, session, question, expose_gap_id=False)
    # 第 47 刀：发问计数（channel 区分顾客/操作者通道；口径见 observability）
    record_chat_request(outcome, channel="customer")
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
    """顾客 thumbs「这条回答有没有帮助」（ADR 0044 §四；第 48 刀放开正反馈）。

    闸序与鉴权同发问：IP 闸（先于鉴权省 DB）-> 401（会话不存在与令牌无效
    统一文案）-> 409（非 active：会话终结后反馈面一并收口）。仅 kind=answer
    且 citations 非空的消息可反馈（拒答/转人工无按钮也不收——404/409）；幂等
    =已反馈 409。

    - ``helpful=false``（第 40 刀）：**分诊**——逐 citation 资产
      ``last_verified_at=None``（「发布=验证快照」被负反馈推翻），复审由治理台
      未验证/stale 面自然承接；commit 后返回分诊资产列表（分诊即答案）。
    - ``helpful=true``（第 48 刀）：**只记不诊**——正反馈的语义是「这条有用」，
      不是「证据要复审」；反向若也撤销验证，等于顾客点赞就进复审队列。
    """
    retry_after = limits.check_ask_ip(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    session = _authorize_customer_session(db, session_id, request)
    # 第 80 刀：ended 下反馈仍放行（善后通道）；此闸只剩 registered 会触发
    if session.status not in (ACTIVE, ENDED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以反馈，当前状态: {session.status}",
        )

    message = db.get(ServiceMessage, message_id)
    if message is None or message.session_id != session_id or message.role != "agent":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
    if message.kind != "answer" or not message.citations:
        # 拒答/转人工/无引用的消息没有反馈入口（ADR 0044：citations 空则分诊
        # 无从谈起；正反馈同理——没有证据可言的回答点「有用」也不构成信号）
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该消息不接受反馈")
    if message.feedback is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该消息已反馈过")

    triaged = triage_asset_ids(message.citations) if body.helpful is False else []
    for asset_id in triaged:
        asset = db.get(Asset, asset_id)
        if asset is not None:
            asset.last_verified_at = None
    message.feedback = {"helpful": body.helpful, "at": datetime.now(UTC).isoformat()}
    db.commit()
    db.refresh(message)
    return FeedbackOut(
        message_id=message.id,
        feedback=dict(message.feedback),
        triaged_asset_ids=triaged,
    )


# ---------- 会话评分（第 48 刀：CSAT） ----------

# 分值合法值集（单一来源）：服务层校验用，也是仪表分布的桶
RATING_SCORES = (1, 2, 3, 4, 5)
_RATING_COMMENT_MAX = 500


class RatingBody(BaseModel):
    score: int
    comment: str | None = None


class RatingOut(BaseModel):
    session_id: int
    score: int
    comment: str | None
    created_at: datetime
    # 第 71 刀：最后一次改评时间（None=首评未改）
    updated_at: datetime | None = None


@router.post("/sessions/{session_id}/rating", response_model=RatingOut)
def rate_session(
    session_id: int,
    body: RatingBody,
    request: Request,
    db: Annotated[Session, Depends(get_db)] = None,
    limits: Annotated[CustomerRateLimits, Depends(get_rate_limits)] = None,
) -> RatingOut:
    """顾客给这次会话打 1–5 星（第 48 刀，CSAT）。

    闸序与发问/反馈一致（IP 闸 -> 401 -> 409 非 active -> 422 分值/留言长度）。
    **评分可改**（第 71 刀，Owner 裁决 2026-09-11）：一行仍唯一，改评是 UPDATE
    **覆盖式留最新**（score/comment 整体覆盖——comment 不带即清空，前端提交时
    回填当前留言故不自清）；updated_at 记最后一次修改。低分不做任何写动作
    （intake 裁决 9）。comment 库内原文；出口（操作者面/仪表）必掩。
    """
    retry_after = limits.check_ask_ip(client_ip(request))
    if retry_after is not None:
        raise _rate_limited(retry_after)

    session = _authorize_customer_session(db, session_id, request)
    # 第 80 刀：ended 下评分仍放行（这是本刀的产品点——结束后评分闭环）
    if session.status not in (ACTIVE, ENDED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以评分，当前状态: {session.status}",
        )
    if body.score not in RATING_SCORES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"评分必须是 {RATING_SCORES[0]}–{RATING_SCORES[-1]} 的整数",
        )
    comment = (body.comment or "").strip() or None
    if comment is not None and len(comment) > _RATING_COMMENT_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"留言不能超过 {_RATING_COMMENT_MAX} 字",
        )
    existing = db.scalar(select(SessionRating).where(SessionRating.session_id == session_id))
    if existing is not None:
        # 第 71 刀：改评=覆盖式留最新（score/comment 整体覆盖，updated_at 记修改）。
        # updated_at 取 DB 钟（func.now() 表达式，flush 时求值）——与 created_at 的
        # server_default 同源；Python 钟/DB 钟混用在 app 与 db 分机部署时会假翻转
        existing.score = body.score
        existing.comment = comment
        existing.updated_at = func.now()
        db.commit()
        db.refresh(existing)
        record_csat_rating(existing.score)
        logger.info("会话改评: session=%s score=%s", session_id, existing.score)
        return RatingOut(
            session_id=existing.session_id,
            score=existing.score,
            comment=existing.comment,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    # 第 71 刀（审计刀 15 B 轴 P2 修订）：首评用**原子 UPSERT**——并发两次提交时，
    # 败者按唯一约束转入 UPDATE（=改评，覆盖式留最新语义的自然延伸），不再吐
    # 语义已过时的 409（与 thumbs 的「非空即已反馈」口径分道：评分可改之后，
    # 「重复提交」不再是冲突而是改评）。
    stmt = pg_insert(SessionRating).values(
        session_id=session_id, score=body.score, comment=comment
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_session_ratings_session_id",
        set_={
            "score": stmt.excluded.score,
            "comment": stmt.excluded.comment,
            "updated_at": func.now(),
        },
    ).returning(
        SessionRating.session_id,
        SessionRating.score,
        SessionRating.comment,
        SessionRating.created_at,
        SessionRating.updated_at,
    )
    row = db.execute(stmt).one()
    db.commit()
    record_csat_rating(row.score)
    logger.info("会话评分(UPSERT): session=%s score=%s", session_id, row.score)
    return RatingOut(
        session_id=row.session_id,
        score=row.score,
        comment=row.comment,
        created_at=row.created_at,
        updated_at=row.updated_at,
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

    # 第 80 刀：ended 下联系方式仍放行（善后通道）；此闸只剩 registered 会触发
    if session.status not in (ACTIVE, ENDED):
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
