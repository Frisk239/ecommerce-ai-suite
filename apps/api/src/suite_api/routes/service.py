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
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import Operator, ServiceMessage, ServiceSession
from suite_api.services.asset_view import AssetDetail, to_asset_detail
from suite_api.services.chat_engine import run_ask, sse_event_stream
from suite_api.services.machine_wash import redact
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/service", tags=["service"])

ACTIVE = "active"
REGISTERED = "registered"

# 列表首问摘要长度
_SUMMARY_CHARS = 60


# ---------- 响应模型（给前端票的契约） ----------


class SessionOut(BaseModel):
    id: int
    status: str
    created_at: datetime
    closed_at: datetime | None
    registered_asset_id: int | None


class SessionSummary(SessionOut):
    first_question: str | None
    message_count: int
    # operator=控制台预览（无令牌）| customer=顾客通道签发（0021；token 非空即 customer）
    origin: str


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


class AskBody(BaseModel):
    content: str


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
    治理台详情页全线流转——在推导源头收口过 redact（先掩后截，若先截后掩，
    跨 60 字边界的手机号会被拦腰咬断逃过正则留裸号前缀），一次收口全链路
    干净。素材任务 title 来自生成文案不走此函数，不动。redact 等长收缩
    （掩码不新增字符），不改变既有摘要长度口径。
    """
    masked = redact(content)
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
    return SessionOut(
        id=session.id,
        status=session.status,
        created_at=session.created_at,
        closed_at=session.closed_at,
        registered_asset_id=session.registered_asset_id,
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
    return SessionDetail(
        id=session.id,
        status=session.status,
        created_at=session.created_at,
        closed_at=session.closed_at,
        registered_asset_id=session.registered_asset_id,
        messages=[_to_message_out(m) for m in _session_messages(db, session.id)],
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
    return StreamingResponse(sse_event_stream(outcome), media_type="text/event-stream")


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
    registered 并指向新资产。登记骨架与文档登记共享 register_asset（此处不
    commit，会话状态变更与其并进同一事务）。source_kind 由本端点定值
    session_backflow（0025：服务端定，不让调用方填报）。不写审计（登记不是
    0005 的 publish/confirm/回滚）。

    顾客会话同样由本端点回流（0021：回流仍是操作者动作，顾客接口不能发布）。
    """
    del operator
    session = _get_session_or_404(db, session_id)
    if session.status != ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有进行中的会话可以回流登记，当前状态: {session.status}",
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
    session.status = REGISTERED
    session.registered_asset_id = asset.id
    session.closed_at = datetime.now(UTC)
    db.commit()
    db.refresh(asset)
    return to_asset_detail(db, asset)
