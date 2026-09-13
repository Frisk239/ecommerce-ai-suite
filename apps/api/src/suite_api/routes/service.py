"""客服会话路由（ADR 0023 / 0021）：操作者预览入口，与顾客接口同一引擎。

- 新开会话 / 列表 / 详情（消息全量含 citations 与 kind）；列表行带 origin
  （customer_token 非空即 customer），客服页对顾客会话显示「顾客」徽章。
- 发问：SSE 流式回答。事件序列 thinking -> delta* -> complete（原型第四节
  冻结的交互状态机只保留 thinking/streaming/stop，传输用 SSE；35ms 逐字与
  mock 大脑不搬）。主体抽到 services/chat_engine（0021：顾客路由同调，行为
  只差鉴权与载荷白名单），拒答（0018 refusal）同事务落知识缺口（0024），
  complete 事件带 gap_id 供前端芯片跳转（ADR 0030：运行时返回，消息表不加列；
  顾客版 complete 不带 gap_id）。
- 厂商生成（第 7 刀，ADR 0033）：检索有证据才调厂商模型流式生成，引用仍由
  服务端从检索命中定（0007）；无证据拒答不调模型（0018）；LLM 未配置/失败
  降级证据组装模板，complete 事件带 fallback（运行时返回，同 gap_id 口径）。
- 回流登记（CONTEXT「会话」词条）：会话转写字节先落对象存储（0013），再建
  kind=dialogue 资产（已接入）+ v1 版本，对话种类无规格必填（0019）、机洗=
  LLM 从转写抽 qa_pairs 草稿（第 12 刀/ADR 0035：未配置模型=弃权降级照常待人洗；
  已配置但失败=停已接入可重试，登记请求内同步等待 ≤20s）；会话置 registered
  并指向登记出的资产。登记不是 0005 三类治理动作，不新增审计 action。登记骨架
  与文档登记共享 services/registration.register_asset（source_kind 由本端点定
  session_backflow）。
- 转人工工单（第 42 刀，ADR 0046 §5）：会话列表带待处理工单计数（批量统计）
  与「全部/待处理工单」分段；会话详情带本会话工单（操作者面掩电话/邮箱）；
  ``POST /handoff-tickets/{id}/resolve`` 结单（pending -> resolved，非法状态
  409）。结单不碰知识缺口（工单与缺口独立，0046 §3）。
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import (
    Asset,
    AssetVersion,
    HandoffTicket,
    Operator,
    ServiceMessage,
    ServiceSession,
    SessionRating,
)
from suite_api.observability import record_chat_request, record_session_transition
from suite_api.services import return_tools
from suite_api.services.asset_view import AssetDetail, to_asset_detail
from suite_api.services.chat_engine import run_ask, sse_event_stream
from suite_api.services.handoff_tickets import (
    resolve_ticket,
    ticket_no,
)
from suite_api.services.machine_wash import redact_contact
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/service", tags=["service"])
logger = logging.getLogger(__name__)

ACTIVE = "active"
REGISTERED = "registered"
# 第 80 刀：顾客可主动结束会话（customer.py end 端点），ended 仍可回流登记。
ENDED = "ended"

# 列表首问摘要长度
_SUMMARY_CHARS = 60


# ---------- 响应模型（给前端票的契约） ----------


class SessionOut(BaseModel):
    id: int
    status: str
    created_at: datetime
    closed_at: datetime | None
    registered_asset_id: int | None
    # 第 54 刀：嵌入宿主的来源站点（过闸的归一 origin；独立访问/操作者预览为 None）。
    # 商家会把 widget 挂到多个站点，靠它分辨「这条会话从哪个站来」。
    host_origin: str | None = None


class SessionSummary(SessionOut):
    first_question: str | None
    message_count: int
    # operator=控制台预览（无令牌）| customer=顾客通道签发（0021；token 非空即 customer）
    origin: str
    # 第 42 刀（ADR 0046 §5）：待处理工单数（status=pending；批量统计非 N+1）。
    # 客服页据此置顶 + 徽章 + 「待处理工单」分段筛选。
    pending_ticket_count: int
    # 第 45b 刀：嵌入小组件的宿主访客 id（第一方 uuid）；独立访问为 None
    visitor_id: str | None = None
    # 第 48 刀：本会话的顾客评分（1–5，未评为 None）——客服页据此显示星级徽章
    rating: int | None = None


class HandoffTicketOut(BaseModel):
    """工单出口（第 42 刀，ADR 0046）。

    操作者面 ``to_handoff_ticket_out(..., mask=True)`` 对 phone/email/note 过
    machine_wash.redact_contact（0046 §6 出口掩，姓名不掩）；顾客面回显自己的
    输入不掩（顾客提交走独立的 ``HandoffContactAck``，不借本掩码模型）。
    """

    id: int
    ticket_no: str  # H-{id:04d}（由 PK 派生，不落列）
    session_id: int
    message_id: int | None
    status: str  # pending | resolved
    name: str | None
    note: str | None
    email: str | None
    phone: str | None
    contact_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


def to_handoff_ticket_out(ticket: HandoffTicket, *, mask: bool) -> HandoffTicketOut:
    """工单 -> 出口模型。mask=True 仅操作者面：phone/email/**note** 过
    machine_wash.redact_contact（ADR 0046 §6 出口必掩——note 是自由文本也可能
    带 PII）；姓名不掩（操作者要称呼对方）。顾客面回显自己的输入不掩。"""
    return HandoffTicketOut(
        id=ticket.id,
        ticket_no=ticket_no(ticket),
        session_id=ticket.session_id,
        message_id=ticket.message_id,
        status=ticket.status,
        name=ticket.name,
        note=redact_contact(ticket.note) if mask else ticket.note,
        email=redact_contact(ticket.email) if mask else ticket.email,
        phone=redact_contact(ticket.phone) if mask else ticket.phone,
        contact_at=ticket.contact_at,
        resolved_at=ticket.resolved_at,
        created_at=ticket.created_at,
    )


class MessageOut(BaseModel):
    id: int
    role: str  # customer | agent
    content: str
    citations: list[dict[str, Any]] | None  # 仅 agent 消息：[{asset_id, version_no}]
    kind: str | None  # answer | refusal | handoff（仅 agent 消息；handoff=0036 工具转人工）
    handoff: bool
    # 0036 工具调用记录 {name, arg, result}（仅订单工具路径的 agent 消息非空）：
    # 回放还原灰底工具条（与 gap_id 的运行时口径不同，随消息落库）
    tool: dict[str, Any] | None
    created_at: datetime


class SessionDetail(SessionOut):
    messages: list[MessageOut]
    # 第 57 刀：详情头部要展示「这段会话从哪来、评了几分」——列表行有的信息
    # （visitor_id / rating）详情一并给，深链进来（?session=N）才看得到同样的上下文
    visitor_id: str | None = None
    rating: int | None = None
    # 第 77 刀：改评时间（None=首评未改）——操作者面「改过」标记的数据面
    rating_updated_at: datetime | None = None

    # 第 42 刀（ADR 0046 §5）：本会话工单（一会话一单，无则 null）。操作者面
    # 出口掩电话/邮箱；详情头部据此渲染「结单」动作。联系方式随工单展示。
    ticket: HandoffTicketOut | None = None


class AskBody(BaseModel):
    content: str


class ConfirmReturnBody(BaseModel):
    """两阶段写阶段二请求（ADR 0044 §一）：确认卡消息 + 阶段一签发的令牌。"""

    message_id: int
    confirmation_token: str


class ConfirmReturnOut(BaseModel):
    message_id: int
    order_no: str
    # 确认后订单全部物流事件（含新追加的确认事件；前端可就地刷新时间轴）
    events: list[dict[str, Any]]


# ---------- 查询辅助 ----------


def _get_session_or_404(db: Session, session_id: int) -> ServiceSession:
    session = db.get(ServiceSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return session


def _session_messages(db: Session, session_id: int) -> list[ServiceMessage]:
    return list(
        db.scalars(
            select(ServiceMessage)
            .where(ServiceMessage.session_id == session_id)
            .order_by(ServiceMessage.id)
        )
    )


def _to_message_out(message: ServiceMessage) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        citations=list(message.citations) if message.citations is not None else None,
        kind=message.kind,
        handoff=message.handoff,
        tool=dict(message.tool) if message.tool is not None else None,
        created_at=message.created_at,
    )


def _first_question(content: str) -> str:
    """60 字首问摘要（会话列表 first_question 与回流登记资产 title 共用）。

    0038 修订（第 21 刀评审处置件 2：title 出口全线）：对话资产 title=顾客
    首问截断，随 to_asset_out / MCP get_asset·export·search / 降级回答标题 /
    治理台详情页全线流转——在推导源头收口掩码（先掩后截，若先截后掩，
    跨 60 字边界的手机号会被拦腰咬断逃过正则留裸号前缀），一次收口全链路
    干净。素材任务 title 来自生成文案不走此函数，不动。掩码等长收缩
    （掩码不新增字符），不改变既有摘要长度口径。

    审计刀 8 P1：改用 `redact_contact`——第 42 刀已证明 `redact` 漏
    `138-0013-8000`（带分隔）与 `ab@x.co`（短域）这类形态，而本函数的产物是
    **对话资产 title**，会经 MCP/导出外流；沿 0038「出口必掩」与 0046 判例收口。
    """
    masked = redact_contact(content)
    return masked[:_SUMMARY_CHARS] + ("…" if len(masked) > _SUMMARY_CHARS else "")


def _origin(session: ServiceSession) -> str:
    # 0021：不加 origin 列，令牌非空即顾客会话（spec Must 1）
    return "customer" if session.customer_token else "operator"


# ---------- 会话 ----------


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> SessionOut:
    del operator  # 动作显式带登录；操作者身份不进会话（运行态实体，ADR 0023）
    session = ServiceSession(status=ACTIVE)
    db.add(session)
    db.commit()
    db.refresh(session)
    record_session_transition("new", ACTIVE)  # 第 84 刀：生命周期迁移可观测
    return SessionOut(
        id=session.id,
        status=session.status,
        created_at=session.created_at,
        closed_at=session.closed_at,
        registered_asset_id=session.registered_asset_id,
        host_origin=session.host_origin,
    )


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[SessionSummary]:
    del operator
    sessions = list(db.scalars(select(ServiceSession).order_by(ServiceSession.id.desc())))
    if not sessions:
        return []
    session_ids = [s.id for s in sessions]
    counts = dict(
        db.execute(
            select(ServiceMessage.session_id, func.count())
            .where(ServiceMessage.session_id.in_(session_ids))
            .group_by(ServiceMessage.session_id)
        ).all()
    )
    firsts: dict[int, str] = {}
    for session_id, content in db.execute(
        select(ServiceMessage.session_id, ServiceMessage.content)
        .where(ServiceMessage.session_id.in_(session_ids), ServiceMessage.role == "customer")
        .order_by(ServiceMessage.id)
    ).all():
        firsts.setdefault(session_id, content)  # 全局 id 升序：setdefault 保留每会话最早一条
    # 第 42 刀（ADR 0046 §5）：第二个 group-by 批量取待处理工单数（不 N+1）
    pending_tickets = dict(
        db.execute(
            select(HandoffTicket.session_id, func.count())
            .where(
                HandoffTicket.session_id.in_(session_ids),
                HandoffTicket.status == "pending",
            )
            .group_by(HandoffTicket.session_id)
        ).all()
    )
    # 第 48 刀：第三个 group-by 批量取会话评分（不 N+1）；未评会话不在字典里
    ratings = dict(
        db.execute(
            select(SessionRating.session_id, SessionRating.score).where(
                SessionRating.session_id.in_(session_ids)
            )
        ).all()
    )
    return [
        SessionSummary(
            id=s.id,
            status=s.status,
            created_at=s.created_at,
            closed_at=s.closed_at,
            registered_asset_id=s.registered_asset_id,
            first_question=_first_question(firsts[s.id]) if s.id in firsts else None,
            message_count=counts.get(s.id, 0),
            origin=_origin(s),
            pending_ticket_count=pending_tickets.get(s.id, 0),
            visitor_id=s.visitor_id,
            host_origin=s.host_origin,
            rating=ratings.get(s.id),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> SessionDetail:
    del operator
    session = _get_session_or_404(db, session_id)
    # 第 42 刀（ADR 0046 §5）：本会话工单（一会话一单）；操作者面掩电话/邮箱
    ticket = db.scalar(select(HandoffTicket).where(HandoffTicket.session_id == session.id))
    # 第 57 刀：评分（一行一会话（第 71 刀起可改，留最新），未评为 None）——详情头部与列表行同口径
    rating_row = db.scalar(select(SessionRating).where(SessionRating.session_id == session.id))
    rating = rating_row.score if rating_row is not None else None
    rating_updated_at = rating_row.updated_at if rating_row is not None else None
    return SessionDetail(
        id=session.id,
        status=session.status,
        created_at=session.created_at,
        closed_at=session.closed_at,
        registered_asset_id=session.registered_asset_id,
        # 第 54/57 刀：来源站点与访客（列表行同源）；第 57 刀补评分
        visitor_id=session.visitor_id,
        rating=rating,
        rating_updated_at=rating_updated_at,
        # 第 54 刀：宿主站点在详情同样要给（审计刀 11 P0：此前详情恒回 None，
        # 与「列表与详情同源」的声称不符——外部消费者会拿到「没有宿主」的错值）
        host_origin=session.host_origin,
        messages=[_to_message_out(m) for m in _session_messages(db, session.id)],
        ticket=to_handoff_ticket_out(ticket, mask=True) if ticket is not None else None,
    )


# ---------- 发问（SSE 流式回答） ----------


@router.post("/sessions/{session_id}/messages")
async def ask(
    session_id: int,
    body: AskBody,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> StreamingResponse:
    """发问 -> SSE 流式回答（thinking -> delta* -> complete）。

    引擎主体在 services/chat_engine.run_ask（0021 同一引擎：本端点只做操作者
    鉴权 + 会话状态/输入校验；先收全再流、commit 后再流、断连=完整落库等取舍见
    该模块 docstring）。complete 带 gap_id（操作者口径，0030）。
    """
    del operator
    session = _get_session_or_404(db, session_id)
    if session.status != ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以继续发问，当前状态: {ServiceSession.STATUS_LABELS.get(session.status, session.status)}",
        )
    question = body.content.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息内容不能为空"
        )

    outcome = await run_ask(db, session, question)
    # 第 47 刀：发问计数（结局+是否厂商生成）。在 route 记而非 SSE 生成器里记——
    # 生成器是懒执行的（客户端不读就不跑），计数不该取决于客户端读没读。
    record_chat_request(outcome, channel="operator")
    # SSE 生成器不碰 DB：返回前归还连接，慢客户端不再钉住池（get_db 幂等 close）
    db.commit()
    db.close()
    return StreamingResponse(sse_event_stream(outcome), media_type="text/event-stream")


# ---------- 两阶段写阶段二（第 40 刀，ADR 0044 §一：确认在人） ----------

# 确认失败 reason -> (HTTP 状态, 文案)：令牌问题 400（请求内容错），订单态
# 问题 409（与当前状态冲突），确认卡缺失 404。
_CONFIRM_RETURN_ERRORS: dict[str, tuple[int, str]] = {
    "invalid_token": (400, "确认令牌无效或已过期"),
    "not_found": (409, "订单不存在"),
    "window_changed": (409, "订单已不在退货窗内，无法确认"),
    "duplicate": (409, "该退货申请已确认过"),
    # 第 44 刀状态闸：状态机不回退（已是退货中或已退款不再受理）
    "status_not_returnable": (409, "订单当前状态不可发起退货（已是退货中或已退款）"),
}


@router.post("/sessions/{session_id}/confirm-return", response_model=ConfirmReturnOut)
def confirm_return(
    session_id: int,
    body: ConfirmReturnBody,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ConfirmReturnOut:
    """操作者对「退货资格」消息确认执行 create_return（两阶段写阶段二）。

    create_return 不在模型注册表——写工具只能经本端点由人确认（ADR 0044：
    资格在代码、确认在人）。校验链：操作者登录 -> 确认卡消息必须属于本会话
    且是 check_return_eligibility 的工具轨迹（404）-> 服务层验签+资格重查
    未变+状态闸+幂等（400/409）-> orders.events 追加确认事件 + **状态迁移为
    退货中**（第 44 刀：退货是状态机成员）-> 同事务落一条 create_return 工具
    轨迹 agent 消息（回放完整：两步工具动作都在会话里，客服页刷新即见
    「已确认」）。返回更新后的事件时间轴。顾客通道无此面
    （get_current_operator 401；两阶段确认是操作者动作）。
    """
    del operator
    _get_session_or_404(db, session_id)
    message = db.get(ServiceMessage, body.message_id)
    if (
        message is None
        or message.session_id != session_id
        or message.role != "agent"
        or message.tool is None
        or message.tool.get("name") != "check_return_eligibility"
        or message.tool.get("rejected")
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="确认卡对应的退货资格消息不存在"
        )
    order_no = str(message.tool.get("arg") or "")
    result = return_tools.confirm_return(db, order_no, body.confirmation_token)
    if not result.get("ok"):
        status_code, detail = _CONFIRM_RETURN_ERRORS.get(
            str(result.get("reason")), (400, "确认失败")
        )
        raise HTTPException(status_code=status_code, detail=detail)
    events = list(result["events"])
    follow_up = ServiceMessage(
        session_id=session_id,
        role="agent",
        content=f"订单 {order_no} 的退货申请已确认，已写入物流事件。",
        citations=[],
        kind="answer",
        handoff=False,
        tool={"name": "create_return", "arg": order_no, "result": "已确认退货 · 事件已写入"},
    )
    db.add(follow_up)
    db.commit()
    db.refresh(follow_up)
    return ConfirmReturnOut(message_id=follow_up.id, order_no=order_no, events=events)


# ---------- 转人工工单结单（第 42 刀，ADR 0046 §5） ----------


@router.post("/handoff-tickets/{ticket_id}/resolve", response_model=HandoffTicketOut)
def resolve_handoff_ticket(
    ticket_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> HandoffTicketOut:
    """操作者结单（pending -> resolved）：人回复后置终态。

    非法状态（已 resolved）由服务层抛 409（照 confirm_return/register 状态机
    先例）；工单不存在 404。没有「反结单」；工单号由 PK 派生随出口带出。
    出口掩电话/邮箱（0038 出口掩，姓名不掩）。
    """
    del operator
    ticket = db.get(HandoffTicket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    resolve_ticket(db, ticket, resolved_at=datetime.now(UTC))
    return to_handoff_ticket_out(ticket, mask=True)


# ---------- 回流登记（CONTEXT「会话」：结束后由操作者回流登记为资产） ----------


@router.post(
    "/sessions/{session_id}/register",
    response_model=AssetDetail,
    status_code=status.HTTP_201_CREATED,
)
def register_session(
    session_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
):
    """回流登记：转写字节先落对象存储（0013 没有字节不能登记）-> 建 kind=dialogue
    资产（已接入）+ v1 版本 -> 机洗=LLM 抽 QA 草稿推进待人洗（未配置模型=弃权
    降级；LLM 失败=停已接入存 last_error，可经重试端点重跑，ADR 0035）-> 会话置
    registered 并指向新资产。登记骨架与文档登记共享 register_asset——注意
    register_asset **内部有自己的 commit**（资产行+版本+字节先落库），会话
    收口是其后第二个事务（原子条件更新；撞并发时孤儿资产按 0042 废弃收口，
    审计刀 16）。source_kind 由本端点定值
    session_backflow（0025：服务端定，不让调用方填报）。不写审计（登记不是
    0005 的 publish/confirm/回滚）。

    顾客会话同样由本端点回流（0021：回流仍是操作者动作，顾客接口不能发布）。
    第 80 刀起 ended（顾客主动结束）的会话也可回流；仅 active 回流时落
    closed_at，ended 的 closed_at 保持顾客结束时刻。
    """
    del operator
    session = _get_session_or_404(db, session_id)
    # 第 80 刀：ended 会话可回流登记（顾客结束只关发问，不关善后/治理通道）；
    # 仅 registered（已回流）与未知态拒绝。
    if session.status not in (ACTIVE, ENDED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有进行中或已结束（未回流）的会话可以回流登记",
        )
    messages = _session_messages(db, session.id)
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="会话没有消息，转写为空，不能登记",
        )

    # 转写：全部消息按时间拼「顾客：…/客服：…」（检索切块按行/轮消费同一格式）
    speaker = {"customer": "顾客", "agent": "客服"}
    transcript = "\n".join(f"{speaker[m.role]}：{m.content}" for m in messages)
    first_customer = next((m for m in messages if m.role == "customer"), None)
    title = _first_question(first_customer.content) if first_customer else "客服对话转写"

    try:
        asset = register_asset(
            db,
            storage,
            kind="dialogue",
            title=title,
            content_bytes=transcript.encode("utf-8"),
            filename=None,
            product_id=None,
            source_kind="session_backflow",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    # 收口用原子条件更新（评审 P1）：register_asset 内部有 commit（机洗 LLM
    # 等待可达 20s），期间顾客可能并发结束会话——读-判-写的旧形态会用窗口前
    # 的内存旧值覆盖（closed_at 被重写成回流钟、或把 ended 半态留下）。
    # WHERE 仍限定可回流状态 + COALESCE 保住顾客结束时刻（终结时刻以首次
    # 为准）；closed_at 统一 DB 钟 func.now()（第 71 刀双钟教训，本刀顺带
    # 把回流路径的 Python 钟一并收口）。
    # 第 84 刀：from 状态在条件更新**之前**取——update 会把内存对象同步成
    # registered，收口后再读就成了 registered->registered。
    register_from = session.status
    rowcount = db.execute(
        update(ServiceSession)
        .where(
            ServiceSession.id == session_id,
            ServiceSession.status.in_((ACTIVE, ENDED)),
            ServiceSession.registered_asset_id.is_(None),
        )
        .values(
            status=REGISTERED,
            registered_asset_id=asset.id,
            closed_at=func.coalesce(ServiceSession.closed_at, func.now()),
        )
    ).rowcount
    if rowcount == 0:
        # 窗口内被并发回流/状态迁移：会话不再指向本次登记——但 register_asset
        # 内部已 commit（资产行+版本+对象字节落地，rollback 撤不回），留着就是
        # 治理队列里的永久孤儿（审计刀 16 B/C 轴 P1 live 复现：并发双回流产出
        # 201+409 与一份 ingested 孤儿资产+孤儿字节）。按 0042 废弃语义补偿
        # 收口：置 discarded_at 隐藏 + 删未发布版本字节（版本行留作审计锚；
        # storage.delete 幂等）。刚登记的资产必未发布，废弃语义合法。
        db.rollback()
        orphan = db.get(Asset, asset.id)
        if orphan is not None and orphan.discarded_at is None:
            orphan.discarded_at = datetime.now(UTC)
            for version in db.scalars(
                select(AssetVersion).where(AssetVersion.asset_id == orphan.id)
            ):
                storage.delete(version.object_key)
            db.commit()
        logger.warning(
            "回流登记撞并发，孤儿资产已废弃收口: session=%s asset=%s", session_id, asset.id
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="会话在登记期间状态已变化（可能已被回流），请刷新后重试",
        )
    db.commit()
    db.refresh(asset)
    # 第 84 刀：只有真发生收口才记（409 冲突路径不记）
    record_session_transition(register_from, REGISTERED)
    return to_asset_detail(db, asset)
