"""客服引擎共享层（ADR 0021：顾客对话接口与控制台预览同一引擎）。

把 routes/service.py 的 ask 主体抽出为两个可复用件，操作者路由与顾客路由
（routes/customer.py）同调，保证两条通道的行为只差在鉴权与载荷白名单：

- ``run_ask(db, session, question, expose_gap_id=True)``：落顾客问句 ->
  检索当前已发布版本 -> 厂商生成（0033）/降级模板 -> 落 agent 消息（引用带
  版本 0007，拒答/转人工显性 0018）-> 拒答同事务落知识缺口（0024），并把拒答
  消息文本升级为交接摘要（第 27 刀：固定文案+问句摘要+缺口 G-xxxx，白名单
  同 complete 载荷的 expose_gap_id——顾客通道不带缺口 ID 段）。
- ``sse_event_stream(outcome, expose_gap_id)``：thinking -> delta* -> complete
  的事件序列（0036 订单工具路径：thinking(查询订单中…) -> tool -> delta* ->
  complete 带 tool）。``expose_gap_id`` 是载荷白名单闸门：操作者保持 True（0030
  运行时返回口径不变）；顾客置 False——complete 不带 gap_id，顾客不暴露内部
  缺口 id（spec 工程裁决：事件载荷白名单裁剪，不是消息表改动）。第 27 刀起
  同一白名单延伸到 run_ask：顾客通道拒答消息文本也不带「缺口：G-xxxx」段
  （两路由同值传入，一个闸管载荷与文本两处）。

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
from suite_api.services.answer import ComposedAnswer, build_refusal_handoff_content, compose_answer
from suite_api.services.knowledge_gaps import record_refusal_gap
from suite_api.services.order_tools import (
    find_order_no,
    get_order_status,
    render_handoff_content,
    render_order_answer,
    summarize_tool_result,
)
from suite_api.services.retrieval import retrieve
from suite_api.services.stock_tools import (
    STOCK_KEYWORD_PATTERN,
    query_stock,
    render_stock_answer,
    render_stock_handoff_content,
    summarize_stock_result,
)

logger = logging.getLogger(__name__)

# UX-NOTES 二点八：检索是本产品的真实动作，比「思考中」更诚实
THINKING_TEXT = "正在检索已发布资产…"
# 第 13 刀（ADR 0036）：订单工具路径的状态行——检索被跳过，状态行必须换成
# 真实动作（同「降级不发生成事件、不装作生成过」的诚实口径）
ORDER_THINKING_TEXT = "查询订单中…"
# 第 14 刀（ADR 0037）：库存工具路径的状态行（同口径，按 outcome.tool.name 区分）
STOCK_THINKING_TEXT = "查询库存中…"
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
    # 0036 订单工具路径：{name, arg, result} 调用记录（工具条数据）；
    # 非工具路径恒为 None——SSE 据此决定 thinking 文案与 tool 事件有无
    tool: dict[str, Any] | None = None


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


async def run_ask(
    db: Session, session: ServiceSession, question: str, *, expose_gap_id: bool = True
) -> AskOutcome:
    """发问主体（调用方已完成鉴权与会话状态校验，question 已 strip 非空）。

    每问独立检索：无多轮记忆（ADR 0023 不预埋）。无命中 -> refusal 消息
    （0018）：固定文案 + handoff=true，不编造不闲聊，且不调模型（防编造省
    调用）。有命中 -> 厂商模型流式生成（0033）；LLM 未配置/失败/空产出 ->
    降级 compose_answer 模板回答（错误细节只进服务端日志，不含密钥）。
    citations 恒由检索命中服务端定（0007；模型无引用决定权）。

    0036 订单工具分派（检索之前的前置正则，不命中零成本、既有路径一行不改）：
    命中 ``SO-\\d+`` -> 只读 get_order_status、跳过检索——查到走模板组装
    answer（不调 LLM、citations=[]），查无/故障走 kind="handoff" 转人工
    （0018 失败不拿检索顶；0024 不产生缺口）。引擎级插入=操作者/顾客双通道
    自动同获（0021 同一引擎）。

    0037 库存工具分派（分派序=订单号 -> 库存工具 -> 检索；第 16 刀修订）：
    订单号优先；库存词表命中 **且** LCS 商品匹配成功（或查询故障 error）才
    走工具、跳过检索——stock>0/==0 走事实 answer 模板（0 是数据不是失败），
    NULL/故障走 kind="handoff"；不调 LLM、citations 恒空、不产生缺口。
    词表命中但商品未命中 -> **回既有检索路径**（不是 handoff，不是工具；
    归宿对齐词条：无证据拒答留缺口，0024）——裸「有货吗」不配吞掉检索。

    ``expose_gap_id``（第 27 刀）：与 sse_event_stream 同名白名单闸——顾客
    路由传 False，拒答消息文本不带「缺口：G-xxxx」段（问句摘要两通道都带）；
    操作者默认 True。缺口本身两通道照常落库（0024 语义不动）。
    """
    # 1) 先落 customer 消息
    db.add(ServiceMessage(session_id=session.id, role="customer", content=question))
    db.commit()

    # 1.5) 订单号命中 -> 工具路径（跳过检索，ADR 0036；单独钉序列）
    order_no = find_order_no(question)
    if order_no is not None:
        return _run_order_ask(db, session, order_no)

    # 1.6) 库存工具分派（ADR 0037 修订，第 16 刀：词表+商品匹配双前置）：
    #      只有商品匹配成功（found）或查询故障（error）才进工具路径；
    #      词表命中但无商品匹配（found=False）回落到下面的检索路径。
    if STOCK_KEYWORD_PATTERN.search(question):
        stock_result = query_stock(db, question)
        if stock_result.get("found") or stock_result.get("error"):
            return _run_stock_ask(db, session, question, stock_result)

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
    # open 缺口复用不新建）。只挂 refusal 路径——工具（0036 订单/0037 库存）
    # 查无/故障转人工不产生缺口（handoff 分支不走本段，契约由单测+集成钉死）。
    # 第 27 刀（词条「转人工」：交接带结构化摘要——拒答面兑现）：拒答消息
    # 文本从固定文案升级为「固定文案 + 问句摘要 +（操作者通道）缺口 G-xxxx」。
    # 拼接点在落库处：gap_id 在拒答消息之后才由 record_refusal_gap 产生
    # （ADR 0030：运行时 id，消息表不加列），同事务内先改属性再 commit，
    # SSE delta 流自然带出全文。REFUSAL_CONTENT 常量结构不变。
    gap: KnowledgeGap | None = None
    if answer.kind == "refusal":
        gap = record_refusal_gap(db, question)
        agent_message.content = build_refusal_handoff_content(
            question, gap.id if expose_gap_id and gap is not None else None
        )
    db.commit()
    db.refresh(agent_message)

    return AskOutcome(
        agent_message=agent_message,
        answer=answer,
        gap=gap,
        generated=generated is not None,
        fallback=fallback,
    )


def _run_order_ask(db: Session, session: ServiceSession, order_no: str) -> AskOutcome:
    """订单工具路径（customer 消息已由 run_ask 落库提交，本函数只落 agent 消息）。

    查无/故障用 kind="handoff"（不复用 refusal——refusal 分支连带
    record_refusal_gap，工具失败不产生缺口，0024/0036）；两条分支都不检索、
    不调 LLM、citations 恒空（订单不是资产，0002）。工具调用记录随消息落列
    （tool JSONB，回放还原工具条），同形状进 SSE tool 事件与 complete.tool。
    序列：2 commit（问句/回答）——与非订单路径独立，单测单独钉。
    """
    result = get_order_status(db, order_no)
    tool_record = {
        "name": "get_order_status",
        "arg": order_no,
        "result": summarize_tool_result(result),
    }
    if result.get("found"):
        kind, handoff = "answer", False
        content = render_order_answer(result)
    else:
        kind, handoff = "handoff", True
        content = render_handoff_content(order_no, result)
        logger.info("订单工具转人工: order_no=%s %s", order_no, tool_record["result"])

    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=content,
        citations=[],
        kind=kind,
        handoff=handoff,
        tool=tool_record,
    )
    db.add(agent_message)
    db.commit()
    db.refresh(agent_message)

    answer = ComposedAnswer(content=content, citations=[], kind=kind, handoff=handoff)
    return AskOutcome(
        agent_message=agent_message,
        answer=answer,
        gap=None,
        generated=False,  # 工具路径不调 LLM：不多发「正在生成回答…」thinking
        fallback=False,  # 非降级——模板组装是工具路径的正式产出（0036 v1）
        tool=tool_record,
    )


def _run_stock_ask(
    db: Session, session: ServiceSession, question: str, result: dict[str, Any]
) -> AskOutcome:
    """库存工具路径（customer 消息已由 run_ask 落库提交；0037=0036 同模式）。

    入口由分派点把已查好的 query_stock 结果传入（found 或 error 才会到这里，
    第 16 刀双前置）。stock>0/==0 用 kind="answer" 事实模板（0 是数据不是
    失败）；stock NULL/DB 异常用 kind="handoff"（不复用 refusal——refusal
    连带缺口，0024 工具失败不产生缺口）。两条分支都不检索、不调 LLM、
    citations 恒空（库存是商品列不是资产，0002）。工具条 arg=命中商品名，
    异常退化用问题原文（既有口径）；序列同订单路径（2 commit）。
    """
    found = bool(result.get("found"))
    stock = result.get("stock")
    tool_record = {
        "name": "get_stock",
        "arg": result["product_name"] if found else question,
        "result": summarize_stock_result(result),
    }
    if found and stock is not None:
        kind, handoff = "answer", False
        content = render_stock_answer(result)
    else:
        kind, handoff = "handoff", True
        content = render_stock_handoff_content(result)
        logger.info("库存工具转人工: %s %s", tool_record["arg"], tool_record["result"])

    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=content,
        citations=[],
        kind=kind,
        handoff=handoff,
        tool=tool_record,
    )
    db.add(agent_message)
    db.commit()
    db.refresh(agent_message)

    answer = ComposedAnswer(content=content, citations=[], kind=kind, handoff=handoff)
    return AskOutcome(
        agent_message=agent_message,
        answer=answer,
        gap=None,
        generated=False,  # 工具路径不调 LLM（0037 与 0036 同口径）
        fallback=False,  # 模板组装是正式产出，非降级
        tool=tool_record,
    )


def sse_event_stream(outcome: AskOutcome, *, expose_gap_id: bool = True) -> Iterator[str]:
    """SSE 事件序列：thinking（检索）[-> thinking（生成）] -> delta* -> complete。

    生成器只吐已收全文本与已落库的元数据，不碰 DB。模型路径多一个 thinking
    （正在生成回答…）；降级不发（诚实标注靠 fallback）。complete 载荷按
    ``expose_gap_id`` 白名单裁剪：顾客通道不吐 gap_id（0030 口径仅对操作者
    保持），操作者通道事件形状与抽取前逐字节一致（新增的 tool 键除外——
    0036：单号本由提问者提供，无内部敏感字段，两通道同形状不裁剪；第 27 刀
    拒答交接摘要改的是 delta **文本**，thinking/tool/complete 事件形状不动）。

    0036 订单工具路径（outcome.tool 非 None）：thinking 换「查询订单中…」
    （检索被跳过，状态行诚实）-> tool {name, arg, result}（工具调用后发，
    工具条数据）-> delta* -> complete（含 tool）。
    """
    if outcome.tool is not None:
        # 0036/0037：检索被跳过，状态行按工具名换成真实动作（订单/库存各自诚实）
        thinking = (
            STOCK_THINKING_TEXT
            if outcome.tool.get("name") == "get_stock"
            else ORDER_THINKING_TEXT
        )
        yield sse_event("thinking", {"text": thinking})
        yield sse_event("tool", outcome.tool)
    else:
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
        # 0036：None 或 {name, arg, result}——非订单路径多一个 null 键，
        # 既有消费方按键取值不受影响；两通道同形状（不走 gap_id 式裁剪）
        "tool": outcome.tool,
    }
    if expose_gap_id:
        # 拒答=缺口 id（前端芯片跳治理台缺口 tab）；answer 恒为 null
        complete["gap_id"] = outcome.gap.id if outcome.gap is not None else None
    # 第 7 刀：true=厂商生成失败降级模板（前端「模板回退」徽章；运行时返回，
    # 同 gap_id 口径，消息表不加列）
    complete["fallback"] = outcome.fallback
    yield sse_event("complete", complete)
