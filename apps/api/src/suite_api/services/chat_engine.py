"""客服引擎共享层（ADR 0021：顾客对话接口与控制台预览同一引擎）。

把 routes/service.py 的 ask 主体抽出为两个可复用件，操作者路由与顾客路由
（routes/customer.py）同调，保证两条通道的行为只差在鉴权与载荷白名单：

- ``run_ask(db, session, question, expose_gap_id=True)``：落顾客问句 ->
  受约束 Agent 步进循环（第 37 刀，ADR 0043，max_steps=3）-> 落 agent 消息
  （引用带版本 0007，拒答/转人工显性 0018）-> 拒答同事务落知识缺口（0024），
  并把拒答消息文本升级为交接摘要（第 27 刀：固定文案+问句摘要+缺口 G-xxxx，
  白名单同 complete 载荷的 expose_gap_id——顾客通道不带缺口 ID 段）。
- ``sse_event_stream(outcome, expose_gap_id)``：thinking -> [tool] -> delta* ->
  complete 的事件序列。``expose_gap_id`` 是载荷白名单闸门：操作者保持 True
  （0030 运行时返回口径不变）；顾客置 False——complete 不带 gap_id，顾客不
  暴露内部缺口 id（spec 工程裁决：事件载荷白名单裁剪，不是消息表改动）。
  第 27 刀起同一白名单延伸到 run_ask：顾客通道拒答消息文本也不带「缺口：
  G-xxxx」段（两路由同值传入，一个闸管载荷与文本两处）。

第 37 刀步进循环（ADR 0043「模型提议、代码授权」，修订 0036/0037 正则前置
分派；max_steps=3，形状固定不设通用循环，步数不落新表——轨迹=消息 tool
记录 + SSE 顺序，可断言可回放）：

- 步 1 快路径（≤1 步）：订单号/库存词表命中 -> 直接工具（零 LLM，0036/0037
  行为零回归；库存仍要求词表+商品双前置）。
- 步 2 提议步（仅未命中快路径）：LLM 按工具描述输出 ``TOOL: 名 {"参数": "值"}``
  提议（格式化约定非 function calling API）——代码校验（agent_tools 注册表
  +参数白名单）授权后执行，工具结果作为 history 附加轮进步 3 生成 prompt；
  提议被拒（越狱/坏参/未注册）-> kind="handoff" 转人工（不检索不调模型不落
  缺口，对齐 0024 工具失败不产生缺口）；模型认为无需工具（纯文本）-> 步 3。
  LLM 不可用/超时/空 key -> 降级为按原问句走步 3（36 刀前行为，不是
  handoff——纯检索可能可答）。
- 步 3 检索+生成步（≤1 步）：照旧（0018 无证据不调生成、0007 citations
  服务端定、检索词按原问句+代词拼接——工具结果不进检索 query）。

取舍（任务锁定并写明，自 routes/service.py 原样搬移）：回答文本在开始流式前
已完整收全并落库——含第 7 刀的厂商模型流（先收全再流，而非边流边攒）：服务
端不存在「部分产出」，断连=客户端停止订阅，agent 消息仍完整入库，SSE 只是
传输；中断（stopped）语义由前端表达。async 是流式收集（await llm.stream_chat）
所需；同步 DB 调用直接在事件循环上跑。LLM 等待前显式 commit 结束只读事务，
20s 等待不得 idle-in-transaction 占连接（写阶段 autobegin 重取）——提议步
（complete_chat 等待）适用同一纪律。

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
from suite_api.services.agent_tools import (
    ToolDecision,
    ToolProposal,
    execute_tool,
    parse_tool_proposal,
    propose_prompt,
)
from suite_api.services.answer import ComposedAnswer, build_refusal_handoff_content, compose_answer
from suite_api.services.catalog_tools import try_catalog_answer
from suite_api.services.conversation_memory import PRONOUN_RE, recent_turns, retrieval_query
from suite_api.services.knowledge_gaps import record_refusal_gap
from suite_api.services.machine_wash import redact
from suite_api.services.order_tools import (
    find_order_no,
    get_order_status,
    render_handoff_content,
    render_order_answer,
    summarize_tool_result,
)
from suite_api.services.retrieval import (
    FIDELITY_MIN_COVERAGE,
    coverage_ratio,
    retrieve,
)
from suite_api.services.return_tools import (
    check_return_eligibility,
    has_return_intent,
    render_eligibility_answer,
    summarize_eligibility,
)
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
# 第 40 刀（ADR 0044 §一）：退货资格工具路径的状态行（同口径，按 tool.name 区分）
RETURN_THINKING_TEXT = "查询退货资格中…"
# 第 41 刀（ADR 0045）：目录回落路径的状态行（retrieve 无命中后读商品行，
# 真实动作；工具式模板组装，不调 LLM）
CATALOG_THINKING_TEXT = "查询商品目录中…"
# 第 7 刀：检索命中后走厂商模型生成（状态行随最新 thinking 事件更新——降级
# 路径不发本事件，状态行停在检索，不装作生成过）
GENERATING_THINKING_TEXT = "正在生成回答…"
# 第 37 刀（ADR 0043）：提议被拒（越狱/坏参/未注册）转人工——无检索无查询
# 发生，状态行只陈述「校验未通过、正在转人工」这一真实动作
REJECTED_THINKING_TEXT = "提议校验未通过，正在转人工…"
# 同口径的固定交接文案（handoff kind，不缺口）
REJECTED_CONTENT = "工具提议被拒绝，已转人工。"
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
    # 非工具路径恒为 None——SSE 据此决定 thinking 文案与 tool 事件有无。
    # 0043 扩展两种来源：提议步合法执行的记录（同形状）；提议被拒的记录
    # （多一个 rejected=True 键与拒绝原因，消费方按键取值不受影响）
    tool: dict[str, Any] | None = None
    # 0043 混意图：工具步执行后接了检索步（SSE 在 tool 后补「正在检索」
    # 状态行——真实动作链，快路径恒为 False）
    retrieved_after_tool: bool = False
    # 第 40 刀（ADR 0044 §二）：忠实度闸触发原因（complete 带 fallback_reason
    # 供观测与校准；普通厂商失败降级为 None——该键不出场，事件形状不破）
    fallback_reason: str | None = None


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

    受约束 Agent 步进循环（模块 docstring「第 37 刀步进循环」段：快路径 ->
    提议步 -> 检索+生成，max_steps=3）。每问独立检索与拒答判定（第 29 刀起
    补会话内多轮记忆：记忆只进生成 prompt 与检索词补全，不改检索/拒答语义）。
    无命中 -> refusal 消息（0018）：固定文案 + handoff=true，不编造不闲聊，
    且不调模型（防编造省调用）。有命中 -> 厂商模型流式生成（0033）；LLM 未
    配置/失败/空产出 -> 降级 compose_answer 模板回答（错误细节只进服务端日志，
    不含密钥）。citations 恒由检索命中服务端定（0007；模型无引用决定权）。

    0036 订单快路径（分派步 1，不命中零成本、既有路径一行不改）：命中
    ``SO-\\d+`` -> 只读 get_order_status、跳过检索——查到走模板组装 answer
    （不调 LLM、citations=[]），查无/故障走 kind="handoff" 转人工（0018 失败
    不拿检索顶；0024 不产生缺口）。引擎级插入=操作者/顾客双通道自动同获
    （0021 同一引擎）。

    0037 库存快路径（步 1 分派序=订单号 -> 库存工具 -> 提议步；第 16 刀修订）：
    订单号优先；库存词表命中 **且** LCS 商品匹配成功（或查询故障 error）才
    走工具、跳过检索——stock>0/==0 走事实 answer 模板（0 是数据不是失败），
    NULL/故障走 kind="handoff"；不调 LLM、citations 恒空、不产生缺口。
    词表命中但商品未命中 -> **进提议步/检索路径**（不是 handoff，不是工具；
    归宿对齐词条：无证据拒答留缺口，0024）——裸「有货吗」不配吞掉检索。

    第 41 刀目录回落（ADR 0045）：步 3 检索返回 [] **且**目录意图（卖什么/
    有什么/目录/多少钱/价格/多少，纯列举或无规格词的报价）时，读商品行做
    列举/报价——工具式模板（不调 LLM、citations 恒空、kind=answer；商品行
    是工具数据源不是引用）。miss（空店/无匹配/无价）沿既有拒答+转人工+
    缺口（去补=上新/改价，回落读实时行价）；显式要真人不抢（留第 42 刀）。

    ``expose_gap_id``（第 27 刀）：与 sse_event_stream 同名白名单闸——顾客
    路由传 False，拒答消息文本不带「缺口：G-xxxx」段（问句摘要两通道都带）；
    操作者默认 True。缺口本身两通道照常落库（0024 语义不动）。

    第 29 刀（feat/multi-turn）：会话内最近轮记忆（最近 N=4 轮，拒答/转人工/
    工具轮整轮跳过，见 conversation_memory）只影响两处——检索词补全（本问
    含代词且会话里有上一轮 -> 上一问+本问拼接检索，retrieve 打分口径不变）
    与生成 prompt 的 history 段（本问含代词才携带）；拒答判定/缺口/工具分派
    语义零改动，无证据仍拒答（记忆不制造证据）。0043 例外：提议步执行的
    工具结果作为 history 附加轮恒进生成 prompt（本问刚刚产生的上下文，与
    代词无关——否则模型综合不出「订单状态+保修政策」双命中回答）。
    """
    # 1) 先落 customer 消息（留引用：多轮记忆取历史时排除本轮刚落的问句）
    customer_message = ServiceMessage(session_id=session.id, role="customer", content=question)
    db.add(customer_message)
    db.commit()

    # 步 1 快路径（零 LLM）：订单号命中 -> 工具路径（跳过检索，ADR 0036）
    order_no = find_order_no(question)
    if order_no is not None:
        # 第 40 刀（ADR 0044 §一）：单号+退货发起意图 -> 阶段一资格查询
        # （同一分派哲学：结构化信号直取工具，零 LLM——「SO-1001 我想退货」
        # 的演示路径由此确定性成立；问进度的照旧走订单工具看事件时间轴）
        if has_return_intent(question):
            return _run_return_eligibility_ask(
                db,
                session,
                ToolProposal(name="check_return_eligibility", args={"order_no": order_no}),
                check_return_eligibility(db, order_no),
            )
        return _run_order_ask(db, session, order_no)

    # 步 1 快路径（续）：库存工具分派（ADR 0037 修订，第 16 刀：词表+商品
    # 双前置）。只有商品匹配成功（found）或查询故障（error）才进工具路径；
    # 词表命中但无商品匹配（found=False）沿用 36 刀既有回落=直接检索（零
    # LLM，不进提议步——词表已是明确信号，ADR 0043「提议步只接无信号问句」）。
    skip_proposal = False
    if STOCK_KEYWORD_PATTERN.search(question):
        stock_result = query_stock(db, question)
        if stock_result.get("found") or stock_result.get("error"):
            return _run_stock_ask(db, session, question, stock_result)
        skip_proposal = True

    # 步 2 提议步（第 37 刀，ADR 0043）：模型按工具描述提议，代码校验授权
    # 执行。history 无条件取（提议步需要会话上下文——单号常以代词在上问）；
    # 取后即 commit：complete_chat 等待（至多 20s）不得 idle-in-transaction
    # 占连接（与生成步同一纪律）。
    history = recent_turns(db, session.id, exclude_message_id=customer_message.id)
    db.commit()
    # 词表命中但商品未命中的回落（skip_proposal）不调 LLM：零成本直取检索步
    decision = ToolDecision() if skip_proposal else await _propose_tool(question, history)

    tool_record: dict[str, Any] | None = None
    tool_history: list[dict[str, str]] = []
    if decision.reject_reason is not None:
        # 越狱/坏参/未注册：拒绝+转人工——不检索、不调模型、不落缺口
        # （对齐 0024 工具路径不产生缺口；轨迹记 rejected 条供回放）
        return _run_rejected_proposal(db, session, decision)
    if decision.proposal is not None:
        # 合法提议 -> 代码授权执行（复用 order/stock 工具既有实现）；
        # 结果作为 history 附加轮进步 3 生成 prompt（检索仍按原问句）
        result = execute_tool(db, decision.proposal, question)
        if decision.proposal.name == "check_return_eligibility":
            # 第 40 刀（ADR 0044 §一）：阶段一资格查询走确定性模板出口（资格+
            # 确认令牌是结构化事实，不进 LLM；阶段二创建由操作者确认端点凭
            # token 执行——create_return 不在注册表，写工具在确认闸之后）
            return _run_return_eligibility_ask(db, session, decision.proposal, result)
        tool_record = _proposal_tool_record(decision.proposal, result, question)
        line = f"[工具结果] {tool_record['name']}({tool_record['arg']})：{tool_record['result']}"
        tool_history = [{"role": "agent", "content": redact(line)}]
    # 纯文本提议 / LLM 降级：直接进步 3（36 刀前行为）

    # 步 3 检索当前已发布版本 -> 组装（模板回答=降级兜底，citations 选取也以
    # 它为准）。检索词补全（第 29 刀）：本问含代词且会话里有上一轮 -> 上一问
    # +本问拼接（retrieve 打分阈值不变）；工具结果不进检索 query（裁决：作为
    # history 附加轮进生成）。
    query_text = retrieval_query(question, history)
    hits = retrieve(db, query_text)
    # 第 41 刀（ADR 0045）：目录回落挂在 compose 空命中拒答分支之前——仅
    # retrieve==[] 且目录意图时列举/报价（工具式模板：不调 LLM、citations 恒
    # 空、kind=answer；商品行是工具数据源不是引用）。miss（无匹配/无价/空店）
    # 返回 None，沿既有拒答+缺口（去补=上新/改价）。忠实度闸与厂商生成跳过
    # 回落命中（模板即正式产出，非降级，fallback=False）。
    catalog = try_catalog_answer(db, question, hits)
    if catalog is not None:
        is_catalog = True
        answer = ComposedAnswer(content=catalog.content, citations=[], kind="answer", handoff=False)
        if tool_record is None:
            tool_record = catalog.tool
    else:
        is_catalog = False
        answer = compose_answer(hits, assets_meta(db, hits))
    # 第 40 刀（ADR 0044 §二）忠实度闸：问句有效 bigram 与命中块并集的交集
    # 占比过低且证据单薄（≤1 条）时不调模型——覆盖不足时模型大概率编造，
    # 降级证据组装模板（复用既有 fallback 徽章通道；fallback_reason 随
    # complete 带出可观测；阈值 FIDELITY_MIN_COVERAGE 为工程初值可校准）。
    # 零命中照旧拒答（0018 不动）；评测 runner 直调 retrieve+compose_answer
    # 不经本闸，评测基线不受影响。
    gate_fallback = (
        not is_catalog
        and answer.kind == "answer"
        and len(hits) <= 1
        and (coverage_ratio(query_text, [hit["chunk"] for hit in hits]) < FIDELITY_MIN_COVERAGE)
    )
    # 结束只读事务：LLM 等待（至多 20s）不得 idle-in-transaction 占连接。
    # 写阶段（agent 消息）autobegin 再取连接。citations 仍以本问检索快照为准。
    db.commit()

    # 步 3（续）厂商生成（第 7 刀，ADR 0033）：有证据才调模型（0018 无证据
    # 不调）。stream_chat 契约：只抛 LLMError 子类（超时/连接已转通用文案）；
    # 空产出视同失败降级。先收全再落库再流式（断连=完整落库契约不变）。
    # 第 29 刀：本问含代词时带会话历史；0043：工具结果附加轮恒带——记忆只改
    # 生成语言（指代消解），citations/tool 语义不变。
    generated: str | None = None
    if answer.kind == "answer" and not gate_fallback and not is_catalog:
        system_prompt, user_prompt = llm.build_prompts(hits, question)
        gen_history = (history if PRONOUN_RE.search(question) else []) + tool_history
        try:
            pieces = [
                piece
                async for piece in llm.stream_chat(
                    system_prompt, user_prompt, history=gen_history or None
                )
            ]
            generated = "".join(pieces).strip() or None
        except llm.LLMError as exc:
            # 错误细节只进服务端日志（llm.stream_chat 已保证消息不含密钥/端点）
            logger.warning("厂商生成失败，降级证据组装模板: %s", type(exc).__name__)
    fallback = answer.kind == "answer" and generated is None and not is_catalog
    content = generated if generated is not None else answer.content

    # 落 agent 消息：引用带版本（0007），拒答/转人工显性（0018）。
    # agent 消息 citations 恒为列表（refusal=[]），customer 消息为 None（ADR 0023「仅 agent」）
    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=content,
        citations=answer.citations,
        kind=answer.kind,
        handoff=answer.handoff,
        tool=tool_record,
    )
    db.add(agent_message)
    # 0024：无证据拒答同事务落知识缺口（question=顾客原问，精确幂等：同文
    # open 缺口复用不新建）。只挂 refusal 路径——工具（0036 订单/0037 库存
    # 快路径查无/故障、0043 提议被拒）转人工不产生缺口（handoff 分支不走本
    # 段，契约由单测+集成钉死）。第 27 刀（词条「转人工」：交接带结构化摘要
    # ——拒答面兑现）：拒答消息文本从固定文案升级为「固定文案 + 问句摘要 +
    # （操作者通道）缺口 G-xxxx」。拼接点在落库处：gap_id 在拒答消息之后才
    # 由 record_refusal_gap 产生（ADR 0030：运行时 id，消息表不加列），同事
    # 务内先改属性再 commit，SSE delta 流自然带出全文。REFUSAL_CONTENT 常
    # 量结构不变。
    gap: KnowledgeGap | None = None
    if answer.kind == "refusal":
        # 带上来源会话（走查修复）：操作者能从缺口抽屉跳回这条原始对话
        gap = record_refusal_gap(db, question, session_id=session.id)
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
        tool=tool_record,
        # 第 41 刀：回落命中的检索发生在工具式模板之前（触发条件即 retrieve
        # 已执行），不补检索状态行——retrieved_after_tool 只属于提议步混意图。
        retrieved_after_tool=tool_record is not None and not is_catalog,
        fallback_reason="coverage" if gate_fallback else None,
    )


async def _propose_tool(question: str, history: list[dict[str, str]]) -> ToolDecision:
    """提议步一次 LLM 调用（complete_chat，与回流抽取同一客户端契约）。

    LLM 不可用/超时/空 key -> 返回空 ToolDecision（=纯文本，降级为按原问句
    走检索步，36 刀前行为；不是 handoff——纯检索可能可答）。被拒时记服务端
    日志（含提议名与原因，轨迹本身随 rejected 工具条落库）。"""
    system_prompt, user_prompt = propose_prompt(question, history)
    try:
        text = await llm.complete_tool_proposal(system_prompt, user_prompt)
    except llm.LLMError as exc:
        logger.warning("提议步 LLM 不可用，降级检索步: %s", type(exc).__name__)
        return ToolDecision()
    decision = parse_tool_proposal(text)
    if decision.reject_reason is not None:
        logger.info("工具提议被拒绝: name=%s reason=%s", decision.raw_name, decision.reject_reason)
    return decision


def _proposal_tool_record(
    proposal: ToolProposal, result: dict[str, Any], question: str
) -> dict[str, Any]:
    """提议步执行的轨迹条（与快路径 tool JSONB 同形状 {name, arg, result}）。

    arg 口径对齐各自工具：订单取命中单号（回退提议参数）；库存取命中商品名
    （未命中回退提议的商品名/原问句，同 _run_stock_ask 口径）。"""
    if proposal.name == "get_stock":
        found = bool(result.get("found"))
        arg = (
            result["product_name"]
            if found and result.get("product_name")
            else (proposal.args.get("product_name") or question)
        )
        return {"name": proposal.name, "arg": arg, "result": summarize_stock_result(result)}
    arg = result.get("order_no") or proposal.args.get("order_no", "")
    return {"name": proposal.name, "arg": arg, "result": summarize_tool_result(result)}


def _run_rejected_proposal(
    db: Session, session: ServiceSession, decision: ToolDecision
) -> AskOutcome:
    """提议被拒（越狱/坏参/未注册）出口：kind="handoff" 转人工。

    不检索、不调模型、不留缺口（0024 同口径：工具通道失败不产生知识缺口，
    由单测+集成钉死计数不变）。轨迹记 rejected 工具条（name=提议名或
    unknown、arg=原始参数 JSON 串、rejected=True、result=拒绝原因）——转人
    工回放时能看到模型到底提议了什么、被哪条校验挡下（ADR 0043 可断言）。"""
    tool_record = {
        "name": decision.raw_name or "unknown",
        "arg": decision.raw_args or "",
        "rejected": True,
        "result": f"提议被拒绝：{decision.reject_reason}",
    }
    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=REJECTED_CONTENT,
        citations=[],
        kind="handoff",
        handoff=True,
        tool=tool_record,
    )
    db.add(agent_message)
    db.commit()
    db.refresh(agent_message)

    answer = ComposedAnswer(content=REJECTED_CONTENT, citations=[], kind="handoff", handoff=True)
    return AskOutcome(
        agent_message=agent_message,
        answer=answer,
        gap=None,
        generated=False,  # 不调模型：不多发「正在生成回答…」thinking
        fallback=False,  # 非降级——转人工是本路径的正式产出
        tool=tool_record,
    )


def _run_return_eligibility_ask(
    db: Session, session: ServiceSession, proposal: ToolProposal, result: dict[str, Any]
) -> AskOutcome:
    """退货资格路径（第 40 刀/ADR 0044 §一 阶段一；customer 消息已由 run_ask
    落库提交，本函数只落 agent 消息）。

    命中且资格可判 -> kind="answer" 确定性模板（token 只进工具条/轨迹——
    操作者客服页据此渲染「确认退货」卡片，顾客可见文本不带 token）；查无/
    故障 -> kind="handoff"（与订单工具同口径：失败不拿检索顶、不产生缺口，
    0024）。不检索、不调 LLM、citations 恒空；阶段二创建由确认端点执行
    （create_return 不在注册表——写工具在操作者确认闸之后）。
    """
    order_no = str(result.get("order_no") or proposal.args.get("order_no", ""))
    tool_record = {
        "name": proposal.name,
        "arg": order_no,
        "result": summarize_eligibility(result),
    }
    if result.get("found"):
        kind, handoff = "answer", False
        content = render_eligibility_answer(result)
    else:
        kind, handoff = "handoff", True
        content = render_handoff_content(order_no, result)
        logger.info("退货资格工具转人工: order_no=%s %s", order_no, tool_record["result"])

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
        generated=False,  # 资格判定是结构化事实，不调 LLM（同订单工具口径）
        fallback=False,  # 模板组装是本路径的正式产出，非降级
        tool=tool_record,
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
    工具条数据）-> delta* -> complete（含 tool）。库存同形状换文案。

    0043 扩展：rejected 工具条（提议被拒）-> thinking「提议校验未通过，
    正在转人工…」（无检索无查询，不装作做过）-> tool -> delta* -> complete
    （kind=handoff）；retrieved_after_tool（混意图：提议工具已执行、检索步
    也真实发生）-> 工具 thinking -> tool -> 检索 thinking -> [生成 thinking]
    -> delta* -> complete（tool+引用并存）。
    """
    if outcome.tool is not None:
        # 0036/0037：状态行按工具名换成真实动作；0043 被拒：只陈述校验与转人工
        if outcome.tool.get("rejected"):
            thinking_text = REJECTED_THINKING_TEXT
        else:
            thinking_text = {
                "get_stock": STOCK_THINKING_TEXT,
                # 第 40 刀：退货资格查询（ADR 0044 阶段一，只读）
                "check_return_eligibility": RETURN_THINKING_TEXT,
                # 第 41 刀：目录回落（ADR 0045，工具式模板）
                "catalog": CATALOG_THINKING_TEXT,
            }.get(str(outcome.tool.get("name")), ORDER_THINKING_TEXT)
        yield sse_event("thinking", {"text": thinking_text})
        yield sse_event("tool", outcome.tool)
        if outcome.retrieved_after_tool:
            # 0043 混意图：工具步之后检索步照常发生——补检索状态行（诚实）
            yield sse_event("thinking", {"text": THINKING_TEXT})
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
    if outcome.fallback_reason is not None:
        # 第 40 刀：忠实度闸触发原因（可观测可校准；普通厂商失败降级不带
        # 该键——运行时返回口径同 fallback，消息表不加列）
        complete["fallback_reason"] = outcome.fallback_reason
    yield sse_event("complete", complete)
