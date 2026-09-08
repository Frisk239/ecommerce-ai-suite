"""客服会话路由（ADR 0023 / 0021）：预览与顾客接口同一引擎，本刀先落 API 侧。

- 新开会话 / 列表 / 详情（消息全量含 citations 与 kind）。
- 发问：SSE 流式回答。事件序列 thinking -> delta* -> complete（原型第四节
  冻结的交互状态机只保留 thinking/streaming/stop，传输用 SSE；35ms 逐字与
  mock 大脑不搬）。拒答（0018 refusal）同事务落知识缺口（0024），complete
  事件带 gap_id 供前端芯片跳转（ADR 0030：运行时返回，消息表不加列）。
- 厂商生成（第 7 刀，ADR 0033）：检索有证据才调厂商模型流式生成，引用仍由
  服务端从检索命中定（0007）；无证据拒答不调模型（0018）；LLM 未配置/失败
  降级证据组装模板，complete 事件带 fallback（运行时返回，同 gap_id 口径）。
- 回流登记（CONTEXT「会话」词条）：会话转写字节先落对象存储（0013），再建
  kind=dialogue 资产（已接入）+ v1 版本，对话种类无规格必填（0019）、机洗无
  字段抽取直接待人洗；会话置 registered 并指向登记出的资产。登记不是 0005
  三类治理动作，不新增审计 action。登记骨架与文档登记共享
  services/registration.register_asset（source_kind 由本端点定 session_backflow）。
"""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import Asset, KnowledgeGap, Operator, ServiceMessage, ServiceSession
from suite_api.services import llm
from suite_api.services.answer import compose_answer
from suite_api.services.asset_view import AssetDetail, to_asset_detail
from suite_api.services.knowledge_gaps import record_refusal_gap
from suite_api.services.registration import register_asset
from suite_api.services.retrieval import retrieve
from suite_platform.storage import ObjectStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/service", tags=["service"])

ACTIVE = "active"
REGISTERED = "registered"

# UX-NOTES 二点八：检索是本产品的真实动作，比「思考中」更诚实
THINKING_TEXT = "正在检索已发布资产…"
# 第 7 刀：检索命中后走厂商模型生成（状态行随最新 thinking 事件更新——降级
# 路径不发本事件，状态行停在检索，不装作生成过）
GENERATING_THINKING_TEXT = "正在生成回答…"
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
async def ask(
    session_id: int,
    body: AskBody,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> StreamingResponse:
    """发问 -> SSE 流式回答（thinking -> delta* -> complete）。

    取舍（任务锁定并写明）：回答文本在开始流式前已完整收全并落库——含第 7 刀
    的厂商模型流（先收全再流，而非边流边攒）：服务端不存在「部分产出」，
    断连=客户端停止订阅，agent 消息仍完整入库，SSE 只是传输；中断（stopped）
    语义由前端表达。async 路由是流式收集（await llm.stream_chat）所需；同步
    DB 调用直接在事件循环上跑（0016 单操作者单店，无并发多写场景），生成等待
    期间 Session 空闲持连接至多 20s（超时上限），属可接受取舍。

    双 commit 取舍：先落 customer 问句再组装落 agent 回答，两个独立 commit。
    agent 组装失败会留下已落库的顾客问句——属可接受残留：问题真实发生过，
    不因回答侧失败而抹掉提问记录。

    无命中 -> refusal 消息（0018）：固定文案 + handoff=true，不编造不闲聊，
    且不调模型（防编造省调用）。有命中 -> 厂商模型流式生成（0033）；LLM 未
    配置/失败/空产出 -> 降级 compose_answer 模板回答，complete 带
    fallback=true（错误细节只进服务端日志，不含密钥）。citations 恒由检索
    命中服务端定（0007；模型无引用决定权）。
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

    # 2) 检索当前已发布版本 -> 组装（模板回答=降级兜底，citations 选取也以它为准）
    hits = retrieve(db, question)
    answer = compose_answer(hits, _assets_meta(db, hits))

    # 2.5) 厂商生成（第 7 刀，ADR 0033）：有证据才调模型（0018 无证据不调）。
    #      stream_chat 契约：只抛 LLMError 子类（超时/连接已转通用文案）；
    #      空产出视同失败降级。先收全再落库再流式（断连=完整落库契约不变）。
    generated: str | None = None
    if answer.kind == "answer":
        system_prompt, user_prompt = llm.build_prompts(hits, question)
        try:
            pieces = [piece async for piece in llm.stream_chat(system_prompt, user_prompt)]
            generated = "".join(pieces).strip() or None
        except llm.LLMError as exc:
            # 错误细节只进服务端日志（llm.stream_chat 已保证消息不含密钥/端点）
            logger.warning("厂商生成失败，降级证据组装模板: %s", type(exc).__name__)
    fallback = answer.kind == "answer" and generated is None
    content = generated if generated is not None else answer.content

    # 3) 落 agent 消息：引用带版本（0007），拒答/转人工显性（0018）。
    #    agent 消息 citations 恒为列表（refusal=[]），customer 消息为 None（ADR 0023「仅 agent」）
    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=content,
        citations=answer.citations,
        kind=answer.kind,
        handoff=answer.handoff,
    )
    db.add(agent_message)
    # 0024：无证据拒答同事务落知识缺口（question=顾客原问，精确幂等：同文
    # open 缺口复用不新建）。只挂 refusal 路径——工具失败转人工不产生缺口
    # （本刀无工具，该契约由集成测试钉死）。
    gap: KnowledgeGap | None = None
    if answer.kind == "refusal":
        gap = record_refusal_gap(db, question)
    db.commit()
    db.refresh(agent_message)

    # 4) SSE 传输：生成器只吐已收全文本与已落库的元数据，不碰 DB。模型路径
    #    多一个 thinking（正在生成回答…）；降级不发（诚实标注靠 fallback）。
    def event_stream() -> Iterator[str]:
        yield _sse_event("thinking", {"text": THINKING_TEXT})
        if generated is not None:
            yield _sse_event("thinking", {"text": GENERATING_THINKING_TEXT})
        for piece in _split_deltas(content):
            yield _sse_event("delta", {"text": piece})
        yield _sse_event(
            "complete",
            {
                "message_id": agent_message.id,
                "citations": answer.citations,
                "kind": answer.kind,
                "handoff": answer.handoff,
                # 拒答=缺口 id（前端芯片跳治理台缺口 tab）；answer 恒为 null
                "gap_id": gap.id if gap is not None else None,
                # 第 7 刀：true=厂商生成失败降级模板（前端「模板回退」徽章；
                # 运行时返回，同 gap_id 口径，消息表不加列）
                "fallback": fallback,
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
    registered 并指向新资产。登记骨架与文档登记共享 register_asset（此处不
    commit，会话状态变更与其并进同一事务）。source_kind 由本端点定值
    session_backflow（0025：服务端定，不让调用方填报）。不写审计（登记不是
    0005 的 publish/confirm/回滚）。
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
