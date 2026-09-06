"""客服会话路由（ADR 0023 / 0021）：预览与顾客接口同一引擎，本刀先落 API 侧。

- 新开会话 / 列表 / 详情（消息全量含 citations 与 kind）。
- 发问：SSE 流式回答。事件序列 thinking -> delta* -> complete（原型第四节
  冻结的交互状态机只保留 thinking/streaming/stop，传输用 SSE；35ms 逐字与
  mock 大脑不搬）。
- 回流登记（CONTEXT「会话」词条）：会话转写字节先落对象存储（0013），再建
  kind=dialogue 资产（已接入）+ v1 版本，对话种类无规格必填（0019）、机洗无
  字段抽取直接待人洗；会话置 registered 并指向登记出的资产。登记不是 0005
  三类治理动作，不新增审计 action。
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import Asset, AssetVersion, Operator, ServiceMessage, ServiceSession
from suite_api.routes.assets import INGESTED, PENDING_REVIEW, AssetDetail, _to_asset_detail
from suite_api.services.answer import compose_answer
from suite_api.services.machine_wash import MachineWashError, run_machine_wash
from suite_api.services.retrieval import retrieve
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/service", tags=["service"])

ACTIVE = "active"
CLOSED = "closed"
REGISTERED = "registered"

# UX-NOTES 二点八：检索是本产品的真实动作，比「思考中」更诚实
THINKING_TEXT = "正在检索已发布资产…"
# 服务端回答分片粒度（~10-20 字/片；打字节奏由前端呈现层控制，服务端不模拟延迟）
_DELTA_CHARS = 12
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


class MessageOut(BaseModel):
    id: int
    role: str  # customer | agent
    content: str
    citations: list[dict[str, Any]] | None  # 仅 agent 消息：[{asset_id, version_no}]
    kind: str | None  # answer | refusal（仅 agent 消息）
    handoff: bool
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
        created_at=message.created_at,
    )


def _first_question(content: str) -> str:
    return content[:_SUMMARY_CHARS] + ("…" if len(content) > _SUMMARY_CHARS else "")


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


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _split_deltas(text: str) -> list[str]:
    return [text[i : i + _DELTA_CHARS] for i in range(0, len(text), _DELTA_CHARS)]


def _assets_meta(db: Session, hits: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    asset_ids = {hit["asset_id"] for hit in hits}
    if not asset_ids:
        return {}
    return {
        asset.id: {"kind": asset.kind, "title": asset.title}
        for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    }


@router.post("/sessions/{session_id}/messages")
def ask(
    session_id: int,
    body: AskBody,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> StreamingResponse:
    """发问 -> SSE 流式回答（thinking -> delta* -> complete）。

    取舍（任务锁定并写明）：回答文本在开始流式前已完整组装并落库——本刀无
    真实 LLM 逐 token 生成，服务端不存在「部分产出」，断连=客户端停止订阅，
    agent 消息仍完整入库，SSE 只是传输；中断（stopped）语义由前端表达。

    无命中 -> refusal 消息（0018）：固定文案 + handoff=true，不编造不闲聊。
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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="消息内容不能为空")

    # 1) 先落 customer 消息（每问独立检索：无多轮记忆，ADR 0023 不预埋）
    db.add(ServiceMessage(session_id=session.id, role="customer", content=question))
    db.commit()

    # 2) 检索当前已发布版本 -> 组装（完整回答在流式开始前产生）
    hits = retrieve(db, question)
    answer = compose_answer(hits, _assets_meta(db, hits))

    # 3) 落 agent 消息：引用带版本（0007），拒答/转人工显性（0018）。
    #    agent 消息 citations 恒为列表（refusal=[]），customer 消息为 None（ADR 0023「仅 agent」）
    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=answer.content,
        citations=answer.citations,
        kind=answer.kind,
        handoff=answer.handoff,
    )
    db.add(agent_message)
    db.commit()
    db.refresh(agent_message)

    # 4) SSE 传输：生成器只吐已组装文本与已落库的元数据，不碰 DB
    def event_stream() -> Iterator[str]:
        yield _sse_event("thinking", {"text": THINKING_TEXT})
        for piece in _split_deltas(answer.content):
            yield _sse_event("delta", {"text": piece})
        yield _sse_event(
            "complete",
            {
                "message_id": agent_message.id,
                "citations": answer.citations,
                "kind": answer.kind,
                "handoff": answer.handoff,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------- 回流登记（CONTEXT「会话」：结束后由操作者回流登记为资产） ----------


@router.post("/sessions/{session_id}/register", response_model=AssetDetail, status_code=status.HTTP_201_CREATED)
def register_session(
    session_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
):
    """回流登记：转写字节先落对象存储（0013 没有字节不能登记）-> 建 kind=dialogue
    资产（已接入）+ v1 版本 -> 机洗（对话无字段抽取，直接待人洗）-> 会话置
    registered 并指向新资产。不写审计（登记不是 0005 的 publish/confirm/回滚）。
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
    data = transcript.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()[:16]
    object_key = f"dialogue/{uuid4().hex}/{digest}.txt"
    storage.put_bytes(object_key, data)

    first_customer = next((m for m in messages if m.role == "customer"), None)
    title = _first_question(first_customer.content) if first_customer else "客服对话转写"

    asset = Asset(kind="dialogue", status=INGESTED, title=title)
    db.add(asset)
    db.flush()  # 拿主键；与文档登记同构，机洗失败也能以 ingested + last_error 落库
    version = AssetVersion(asset_id=asset.id, version_no=1, object_key=object_key)
    db.add(version)
    try:
        # 对话种类无规格必填（0019）：字段集为空，机洗=解析转写 -> 直接待人洗
        extracted = run_machine_wash(storage, object_key, [])
        version.extracted_fields = extracted
        asset.status = PENDING_REVIEW
    except (MachineWashError, FileNotFoundError) as exc:
        asset.status = INGESTED
        asset.last_error = str(exc)[:500] or exc.__class__.__name__

    session.status = REGISTERED
    session.registered_asset_id = asset.id
    session.closed_at = datetime.now(UTC)
    db.commit()
    db.refresh(asset)
    return _to_asset_detail(db, asset)
