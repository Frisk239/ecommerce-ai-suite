"""客服引擎共享层（ADR 0021：顾客对话接口与控制台预览同一引擎）。

把 routes/service.py 的 ask 主体抽出为两个可复用件，操作者路由与顾客路由
（routes/customer.py）同调，保证两条通道的行为只差在鉴权与载荷白名单：

- ``run_ask(db, session, question)``：落顾客问句 -> 检索当前已发布版本 ->
  厂商生成（0033）/降级模板 -> 落 agent 消息（引用带版本 0007，拒答/转人工
  显性 0018）-> 拒答同事务落知识缺口（0024）。
- ``sse_event_stream(outcome, expose_gap_id)``：thinking -> delta* -> complete
  的事件序列。``expose_gap_id`` 是载荷白名单闸门：操作者保持 True（0030 运行
  时返回口径不变）；顾客置 False——complete 不带 gap_id，顾客不暴露内部
  缺口 id（spec 工程裁决：事件载荷白名单裁剪，不是消息表改动）。

取舍（任务锁定并写明，自 routes/service.py 原样搬移）：回答文本在开始流式前
已完整收全并落库——含第 7 刀的厂商模型流（先收全再流，而非边流边攒）：服务
端不存在「部分产出」，断连=客户端停止订阅，agent 消息仍完整入库，SSE 只是
传输；中断（stopped）语义由前端表达。async 是流式收集（await llm.stream_chat）
所需；同步 DB 调用直接在事件循环上跑。LLM 等待前显式 commit 结束只读事务，
20s 等待不得 idle-in-transaction 占连接（写阶段 autobegin 重取）。

双 commit 取舍：先落 customer 问句再组装落 agent 回答，两个独立 commit。agent
组装失败会留下已落库的顾客问句——属可接受残留：问题真实发生过，不因回答侧
失败而抹掉提问记录。LLM 前再 commit 一次只为归还连接，不改变「问句与回答
分两事务」的语义。
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, KnowledgeGap, ServiceMessage, ServiceSession
from suite_api.services import llm
from suite_api.services.answer import ComposedAnswer, compose_answer
from suite_api.services.knowledge_gaps import record_refusal_gap
from suite_api.services.retrieval import retrieve

logger = logging.getLogger(__name__)

# UX-NOTES 二点八：检索是本产品的真实动作，比「思考中」更诚实
THINKING_TEXT = "正在检索已发布资产…"
# 第 7 刀：检索命中后走厂商模型生成（状态行随最新 thinking 事件更新——降级
# 路径不发本事件，状态行停在检索，不装作生成过）
GENERATING_THINKING_TEXT = "正在生成回答…"
# 服务端回答分片粒度（~10-20 字/片；打字节奏由前端呈现层控制，服务端不模拟延迟）
_DELTA_CHARS = 12


@dataclass(frozen=True)
class AskOutcome:
    """一次发问的完整产出：SSE 事件流所需的全部已落库元数据。"""

    agent_message: ServiceMessage
    answer: ComposedAnswer
    # 0024：无证据拒答同事务落的知识缺口（answer 路径恒为 None）
    gap: KnowledgeGap | None
    # 厂商生成是否产出全文（生成路径多一个 thinking 事件）
    generated: bool
    # True=厂商生成失败/未配置降级证据组装模板（前端「模板回退」徽章）
    fallback: bool


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def split_deltas(text: str) -> list[str]:
    return [text[i : i + _DELTA_CHARS] for i in range(0, len(text), _DELTA_CHARS)]


def assets_meta(db: Session, hits: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    asset_ids = {hit["asset_id"] for hit in hits}
    if not asset_ids:
        return {}
    return {
        asset.id: {"kind": asset.kind, "title": asset.title}
        for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    }


async def run_ask(db: Session, session: ServiceSession, question: str) -> AskOutcome:
    """发问主体（调用方已完成鉴权与会话状态校验，question 已 strip 非空）。

    每问独立检索：无多轮记忆（ADR 0023 不预埋）。无命中 -> refusal 消息
    （0018）：固定文案 + handoff=true，不编造不闲聊，且不调模型（防编造省
    调用）。有命中 -> 厂商模型流式生成（0033）；LLM 未配置/失败/空产出 ->
    降级 compose_answer 模板回答（错误细节只进服务端日志，不含密钥）。
    citations 恒由检索命中服务端定（0007；模型无引用决定权）。
    """
    # 1) 先落 customer 消息
    db.add(ServiceMessage(session_id=session.id, role="customer", content=question))
    db.commit()

    # 2) 检索当前已发布版本 -> 组装（模板回答=降级兜底，citations 选取也以它为准）
    hits = retrieve(db, question)
    answer = compose_answer(hits, assets_meta(db, hits))
    # 结束只读事务：LLM 等待（至多 20s）不得 idle-in-transaction 占连接。
    # 写阶段（agent 消息）autobegin 再取连接。citations 仍以本问检索快照为准。
    db.commit()

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

    return AskOutcome(
        agent_message=agent_message,
        answer=answer,
        gap=gap,
        generated=generated is not None,
        fallback=fallback,
    )


def sse_event_stream(outcome: AskOutcome, *, expose_gap_id: bool = True) -> Iterator[str]:
    """SSE 事件序列：thinking（检索）[-> thinking（生成）] -> delta* -> complete。

    生成器只吐已收全文本与已落库的元数据，不碰 DB。模型路径多一个 thinking
    （正在生成回答…）；降级不发（诚实标注靠 fallback）。complete 载荷按
    ``expose_gap_id`` 白名单裁剪：顾客通道不吐 gap_id（0030 口径仅对操作者
    保持），操作者通道事件形状与抽取前逐字节一致。
    """
    yield sse_event("thinking", {"text": THINKING_TEXT})
    if outcome.generated:
        yield sse_event("thinking", {"text": GENERATING_THINKING_TEXT})
    for piece in split_deltas(outcome.agent_message.content):
        yield sse_event("delta", {"text": piece})
    complete: dict[str, Any] = {
        "message_id": outcome.agent_message.id,
        "citations": outcome.answer.citations,
        "kind": outcome.answer.kind,
        "handoff": outcome.answer.handoff,
    }
    if expose_gap_id:
        # 拒答=缺口 id（前端芯片跳治理台缺口 tab）；answer 恒为 null
        complete["gap_id"] = outcome.gap.id if outcome.gap is not None else None
    # 第 7 刀：true=厂商生成失败降级模板（前端「模板回退」徽章；运行时返回，
    # 同 gap_id 口径，消息表不加列）
    complete["fallback"] = outcome.fallback
    yield sse_event("complete", complete)
