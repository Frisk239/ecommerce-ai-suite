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
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, HandoffTicket, KnowledgeGap, ServiceMessage, ServiceSession
from suite_api.observability import observe_first_token
from suite_api.services import llm
from suite_api.services.agent_tools import (
    ToolDecision,
    ToolProposal,
    execute_tool,
    parse_tool_proposal,
    propose_prompt,
)
from suite_api.services.answer import (
    REFUSAL_CONTENT,
    ComposedAnswer,
    build_refusal_handoff_content,
    compose_answer,
)
from suite_api.services.catalog_tools import try_catalog_answer, try_price_answer
from suite_api.services.conversation_memory import PRONOUN_RE, recent_turns, retrieval_query
from suite_api.services.handoff_tickets import (
    ensure_session_ticket,
    render_handoff_receipt,
    ticket_no,
    wants_human,
)
from suite_api.services.knowledge_gaps import (
    record_refusal_gap,
    resolve_gap_answered_by_catalog,
)
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
    is_opinion_question,
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
from suite_api.settings import get_settings

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
# 第 70 刀：澄清路径的状态行——没有发生订单查询，不能沿用「查询订单中…」
CLARIFY_THINKING_TEXT = "正在核对订单信息…"
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
# 第 42 刀（ADR 0046）：转人工路径的状态行——本次真实动作是建/取工单并回执，
# 没有检索也没有工具查询，状态行必须诚实（同 REJECTED_THINKING_TEXT 口径）
HANDOFF_THINKING_TEXT = "正在转接人工…"
# 服务端回答分片粒度（~10-20 字/片；打字节奏由前端呈现层控制，服务端不模拟延迟）
_DELTA_CHARS = 12

# 写动作凭证（两阶段写的 confirmation_token，ADR 0044）：它只该出现在**操作者面**
# （确认端点要用），**绝不下发顾客**——「顾客与模型都不掌握创建权」是那套设计的前提。
# 审计刀 8 P1：此前工具条 result 把 "待确认 token=<hex>" 原样随 SSE 两通道下发，
# 顾客浏览器里能看到这份写凭证（端点仍有操作者鉴权，故不可直接利用，但边界已破）。
_WRITE_CREDENTIAL_RE = re.compile(r"\s*·?\s*待确认\s*token=[0-9a-f]+")


def customer_safe_tool(tool: dict[str, Any] | None) -> dict[str, Any] | None:
    """顾客通道下发的工具轨迹：剥掉写动作凭证，其余形状不变（None 原样）。

    通道判据沿用 `expose_gap_id`（它本就是「顾客白名单」开关，见 run_ask/gap_id
    先例）；库里持久化的 tool 列不动——操作者重开会话仍能拿到令牌去确认。
    """
    if tool is None:
        return None
    result = tool.get("result")
    if not isinstance(result, str) or "token=" not in result:
        return tool
    return {**tool, "result": _WRITE_CREDENTIAL_RE.sub("", result).strip()}


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
    # 第 42 刀（ADR 0046）：本会话工单（handoff/拒答路径非 None；answer/工具
    # 查得路径恒 None）。SSE complete 据此带工单号回执（运行时可选键）。
    ticket: HandoffTicket | None = None


# 覆盖声明（第 58 刀）：模型自己说「证据没覆盖/没有相关信息」——**保守**只认
# 「证据类主语 + 未覆盖类谓语」与「无法回答/提供」两种说法；模型真在回答时不会
# 这么写（系统提示要求只依据证据作答）。
_NO_COVERAGE_RE = re.compile(
    # **主语必须是证据类名词**（证据/资料/文档/资产）——裸「信息/数据」会把
    # 「配料信息中不包含任何防腐剂」这种**事实否定句**误判成覆盖声明（审计刀 12：
    # 误判会把答对的整条收成拒答+落缺口，后果比漏检更重）
    # 谓语面覆盖常见同义说法：未覆盖/未涉及/未包含/不包含/中没有/没有提及/
    # **未提供/没有提供/未收录/尚未收录**（审计刀 12 实测「现有证据未提供…」漏检，
    # 那条是老行为「未覆盖却挂引用」的回归）
    r"(证据|资料|文档|资产)[^。；]{0,12}"
    r"(未覆盖|未涉及|未包含|不包含|未提供|未收录|尚未收录|未说明|未提到|未给出|未列明"
    r"|中(都)?没有|没有提及|没有提供|没有说明)"
    # 第二支要求宾语**紧贴**动词（不许跨填充词）：「无法提供价格」命中，
    # 「无法提供比这更详细的信息」不命中（那是正常措辞，不是覆盖声明）
    r"|无法(回答|提供|确认|给出)(该|这个|此)?(问题|价格|答案|信息)"
)

# 句子切分（。；！？与换行）：**按句判覆盖声明**，不按整段判——审计刀 12 P0：
# 模型对「怎么退货？」常输出「签收后 7 天内可申请退货。[1][2] 具体退货操作证据
# 未覆盖。」——整段命中就全弃，把**已被证据支撑的那句**也丢了（内置建议问句随机
# 变拒答）。改成：摘掉免责句后若还剩实质内容，就照常作答。
_SENTENCE_SPLIT_RE = re.compile(r"[^。；！？\n]+[。；！？]?")


_CITE_MARK_RE = re.compile(r"\[\d+\]")
_SUBSTANTIVE_RE = re.compile(r"[0-9A-Za-z一-鿿]")


def merge_own_hits(hits: list[dict[str, Any]], own: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """拼接检索的**本问保底**（审计刀 13 C 轴 P0-1，纯函数便于单测）。

    拼接生效时（问句含代词且会上问），上一问的主题块常占满 top-k，本问真正问的
    主题（如「材质」）被挤出上下文——「净含量→那它的材质是什么」拒答转人工。
    本问裸检索的新命中去重后与拼接结果**交错**：prompt 与引用选取都只看前两条
    （`_MAX_PROMPT_EVIDENCE`/`_MAX_EVIDENCE`=2），追加在尾部等于没并——交错后
    证据窗是「拼接首位（语境：哪个商品）+ 本问首位（主题：问什么）」。
    """
    seen = {(h["asset_id"], h["version_no"], h["chunk"]) for h in hits}
    fresh = [h for h in own if (h["asset_id"], h["version_no"], h["chunk"]) not in seen][:2]
    merged: list[dict[str, Any]] = []
    for index, hit in enumerate(hits):
        merged.append(hit)
        if index < len(fresh):
            merged.append(fresh[index])
    merged.extend(fresh[len(hits):])
    return merged


def strip_coverage_disclaimers(text: str) -> tuple[str, bool]:
    """按句摘掉覆盖声明句，返回 (剩余正文, 是否**只剩**免责句)。

    - 逐句判 `_NO_COVERAGE_RE`；留下非免责句（保持原顺序、原标点）。
    - 剩余正文**去掉引用标记后仍有实质字符** -> `(正文, False)`：照常作答。
    - 否则（全被摘光，或只剩 `[1][2]` 这类引用标记）-> `("", True)`：按拒答收口。
      （审计刀 12：只摘掉句子不看残留内容，会把「已发布证据未说明 X。[1][2]」这种
      单句免责答成一段光秃秃的 `[1][2]`。）
    """
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.findall(text):
        if _NO_COVERAGE_RE.search(sentence):
            continue
        kept.append(sentence)
    remaining = "".join(kept).strip()
    if not _SUBSTANTIVE_RE.search(_CITE_MARK_RE.sub("", remaining)):
        return "", True
    return remaining, False


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

    第 42 刀转人工真闭环（ADR 0046）：步 0 词表快路径（显式要真人/投诉/举报，
    含裸「人工」不含——见 handoff_tickets）插在一切工具之前；模型提议步识别
    ``TOOL: handoff {}`` sentinel 走同一出口；拒答路径同事务建/取工单（消息
    文本不动）。三处都经 ensure_session_ticket 幂等建/取本会话唯一工单，回执
    带 H-xxxx（运行时可选键 ticket_id/ticket_no），前端据此挂联系方式表单。
    工单是「顾客要人」，缺口是「知识待补」，同一会话可并存不合并（0046 §3）。

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

    # 步 0 词表快路径（第 42 刀，ADR 0046 裁决 4）：显式要真人/投诉/举报优先于
    # 一切工具——插在订单/库存快路径之前。词表不含裸「人工」（会误伤「人工智能」，
    # 见 handoff_tickets.HUMAN_REQUEST_RE）。命中即建/取本会话工单并回执，不检索
    # 不调模型不落缺口（工单是服务问题、缺口是知识问题，0046 §3）。
    if wants_human(question):
        return _run_handoff_ask(db, session, question, source="词表")

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

    # 步 1（续，第 70 刀）：**订单状态问但没带单号** -> 请求订单号（不检索）。
    # 此前这类问句落到检索，弱命中回流对话块后半答（复审审计 F3：「我的订单
    # 到哪了」引一条自述「未覆盖订单进度」的资产）；拒答+缺口也不对——缺的
    # 不是知识是**输入**。观点问（物流怎么样）不触发：评论正是它的证据。
    # 长度闸（≤14 字）：状态问的口语形态都是短问；「我的订单到哪了？顺便讲讲
    # 保修政策」这类**混意图**问句更长，留给提议步综合（ADR 0043 的混合分派），
    # 澄清不能截胡它。
    if (
        len(question) <= _ORDER_CLARIFY_MAX_CHARS
        and _ORDER_STATE_CLARIFY_RE.search(question)
        and not _ORDER_CLARIFY_MIXED_RE.search(question)
        and not is_opinion_question(question)
    ):
        return _run_order_clarify_ask(db, session, question)

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
    if decision.handoff:
        # 第 42 刀（ADR 0046 §2）：模型识别出顾客明确要人 -> 与词表快路径同一
        # 出口（建/取工单 + 回执消息）。handoff 是 sentinel 不在 registry，
        # 解析已在 parse_tool_proposal 里先识别，不会落「未注册工具」被拒。
        return _run_handoff_ask(db, session, question, source="提议")
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
    # 评论适用域闸按**本问**判（第 66 刀评审 P1）：拼接串带上问的观点标记会
    # 豁免本问的服务态问句（「物流怎么样→它到货了吗」放过 garbage 评论）。
    hits = retrieve(db, query_text, gate_question=question)
    # 审计刀 13 C 轴 P0-1：拼接检索会让**上一问**的主题块占满 top-k（实测
    # 「净含量→那它的材质是什么」时「材质：钛钢」全被净含量块挤出，模型上下文
    # 里确无材质证据，只能诚实拒答——README 演示口径的追问演不出来）。补一条
    # **本问保底**：拼接生效时再按本问裸检索一次，新命中去重后并入（上限 2 条）——
    # 上一问给语境（哪个商品），本问给主题（问什么），两边都在上下文里。只影响
    # 带代词的追问（首问/无代词不触发）；retrieve 打分口径与阈值不动。
    if query_text != question:
        hits = merge_own_hits(hits, retrieve(db, question))
    # 第 41 刀（ADR 0045）：目录回落挂在 compose 空命中拒答分支之前——仅
    # retrieve==[] 且目录意图时列举/报价（工具式模板：不调 LLM、citations 恒
    # 空、kind=answer；商品行是工具数据源不是引用）。第 50 刀修订：**报价**
    # 不再要求空命中（见下一段的 try_price_answer）。miss（无匹配/无价/空店/
    # 纯度不足）返回 None，沿既有拒答+缺口（去补=上新/改价）。忠实度闸与厂商
    # 生成跳过回落命中（模板即正式产出，非降级，fallback=False）。
    catalog = try_catalog_answer(db, question, hits)
    if catalog is None:
        # 第 50 刀（ADR 0045 修订）：报价意图不看检索命中——价格是商品行的事实，
        # 而演示库几乎任何问句都有命中，导致「X 多少钱」永远走 RAG 答「证据未
        # 覆盖价格」（行价白补齐了）。只对报价生效：列举仍要空命中闸。
        catalog = try_price_answer(db, question)
    if catalog is not None:
        is_catalog = True
        answer = ComposedAnswer(content=catalog.content, citations=[], kind="answer", handoff=False)
        if tool_record is None:
            tool_record = catalog.tool
        # 第 56 刀（审计刀 11 P0-1）：目录能答 = 这条不是「知识待补」——同问的
        # open 缺口一并收掉（否则治理台「待补」写着客服当下已经能答的问题，
        # 点「去补文档」还把人引去补一份不需要的文档）。同事务，随本问一起落库。
        resolve_gap_answered_by_catalog(db, question)
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
            pieces: list[str] = []
            # 第 47 刀：首个生成增量处记一次 TTFT（口径=请求进入中间件 → 首字，
            # 含检索/提议/工具步；非生成路径不记）。直调 run_ask 的测试没有请求
            # 起点，observe_first_token 内部会跳过——不记假值。
            async for piece in llm.stream_chat(
                system_prompt, user_prompt, history=gen_history or None
            ):
                if not pieces:
                    observe_first_token(get_settings().llm_model)
                pieces.append(piece)
            generated = "".join(pieces).strip() or None
        except llm.LLMError as exc:
            # 错误细节只进服务端日志（llm.stream_chat 已保证消息不含密钥/端点）
            logger.warning("厂商生成失败，降级证据组装模板: %s", type(exc).__name__)
    # 第 58 刀（审计刀 11 C-P1-1）：「弱命中 -> 模型自述证据未覆盖」也是一种**答不了**。
    # 此前它被记成 kind=answer（还挂着引用），既不落缺口也不转人工——同一处知识
    # 缺失因为「检索有没有边际命中」产生两种系统状态（无命中：拒答+缺口；有弱命中：
    # 「已答」+引用）。收口：检出覆盖声明就按拒答处理（缺口照落、转人工照建、
    # citations 清零、文案仍用既有拒答模板——一个知识洞只有一种形态）。
    no_coverage = False
    if answer.kind == "answer" and generated is not None:
        # **按句**判（审计刀 12 P0）：模型对「怎么退货？」常输出「签收后 7 天内可
        # 申请退货。[1][2] 具体退货操作证据未覆盖。」——整段命中就全弃会把**已被
        # 证据支撑的那句**也丢掉（内置建议问句随机变拒答）。改成：先摘免责句，
        # 还有实质内容就照常作答；只剩免责句才按拒答收口。
        remaining, all_disclaimers = strip_coverage_disclaimers(generated)
        if all_disclaimers:
            no_coverage = True
            answer = ComposedAnswer(
                content=REFUSAL_CONTENT, citations=[], kind="refusal", handoff=True
            )
            generated = None  # 用拒答模板落库（内容不是模型那句自述）
        elif remaining != generated:
            generated = remaining  # 免责句摘掉，正文与引用照旧
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
    ticket: HandoffTicket | None = None
    if answer.kind == "answer":
        # 审计刀 12 P1：**任何答上的出口都关同问 open 缺口**——此前只有目录分支调，
        # 于是「库存已答」「RAG 已答」的问句仍挂着「待补」（点「去补文档」误人）。
        resolve_gap_answered_by_catalog(db, question)
    if answer.kind == "refusal":
        # 带上来源会话（走查修复）：操作者能从缺口抽屉跳回这条原始对话
        gap = record_refusal_gap(db, question, session_id=session.id)
        agent_message.content = build_refusal_handoff_content(
            question, gap.id if expose_gap_id and gap is not None else None
        )
        # 第 42 刀（ADR 0046 §2）：拒答也建/取工单——前端见 handoff=true 已亮
        # 「已转人工」徽章，必须真有东西接住。同事务 create-or-get；**拒答消息
        # 文本一个字符都不改**（REFUSAL_CONTENT 与既有全等断言保持）。
        ticket = ensure_session_ticket(db, session, message=agent_message)
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
        fallback_reason=(
            "coverage" if gate_fallback else ("no_coverage" if no_coverage else None)
        ),
        ticket=ticket,
    )


def _run_handoff_ask(
    db: Session, session: ServiceSession, question: str, *, source: str
) -> AskOutcome:
    """显式转人工出口（词表快路径与模型提议共用，ADR 0046 §2）。

    建/取本会话工单（幂等，一会话一单）-> 落 kind="handoff" 消息（回执带实际
    工单号）-> 同一事务提交。不检索、不调模型、不落缺口（工单是服务问题、
    缺口是知识问题，0046 §3）。工具条 ``{name:"handoff", arg:来源, result:
    工单号}``——与订单/库存工具同为「已发生的动作留档」，重载可回放；顾客联系
    方式表单由前端按 handoff 旗标挂在这条消息下。

    ``question`` 保留在签名里与其它 _run_* 出口同形（本路径不消费问句）。
    ``source`` 是轨迹 arg（词表/提议），回放时能看出哪条路径接住。
    """
    del question  # 本路径不消费问句；保留参数与其它出口同签
    ticket = ensure_session_ticket(db, session)  # 建单并 flush 拿 PK（回执要号码）
    no = ticket_no(ticket)
    tool_record = {"name": "handoff", "arg": source, "result": f"已记录工单 {no}"}
    content = render_handoff_receipt(no)
    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=content,
        citations=[],
        kind="handoff",
        handoff=True,
        tool=tool_record,
    )
    db.add(agent_message)
    db.flush()  # 拿消息 PK
    if ticket.message_id is None:
        # 触发它的第一条 agent 消息（表单挂它下面）；旧工单复用不覆盖锚点
        ticket.message_id = agent_message.id
    db.commit()
    db.refresh(agent_message)
    db.refresh(ticket)

    answer = ComposedAnswer(content=content, citations=[], kind="handoff", handoff=True)
    return AskOutcome(
        agent_message=agent_message,
        answer=answer,
        gap=None,
        generated=False,  # 不调模型：不多发「正在生成回答…」thinking
        fallback=False,  # 非降级——转人工是本路径的正式产出
        tool=tool_record,
        ticket=ticket,
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


def _ensure_handoff_ticket(
    db: Session, session: ServiceSession, agent_message: ServiceMessage, handoff: bool
) -> HandoffTicket | None:
    """工具失败转人工出口共用（第 42 刀，ADR 0046 补口）：凡是亮「已转人工」
    徽章的路径，背后都必须有工单。

    同事务建/取本会话唯一工单（幂等），工单挂在第一条触发 agent 消息上；
    handoff=False 的 answer 分支恒 None。**不改这些路径的既有文案与工具轨迹**
    （order/stock/return 的精确文案断言保持绿）。同 ensure_session_ticket 的
    SAVEPOINT 兜底，绝不在本函数里 commit/rollback——调用方随后统一 commit，
    与 agent 消息同事务。
    """
    if not handoff:
        return None
    return ensure_session_ticket(db, session, message=agent_message)


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
    # 转人工出口建/取工单（第 42 刀补口）：徽章背后必须有工单
    ticket = ensure_session_ticket(db, session, message=agent_message)
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
        ticket=ticket,
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
    # 查无/故障走 handoff -> 建/取工单（answer 分支 _ensure_handoff_ticket 恒 None）
    ticket = _ensure_handoff_ticket(db, session, agent_message, handoff)
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
        ticket=ticket,
    )


# 第 70 刀：订单状态问但无单号的澄清路由。触发面刻意收窄（订单/查物流/到哪了/
# 到货了吗 形态）——「物流怎么样」走观点豁免（评论答）、「什么时候能收到货」
# 不触发（时效政策可由文档答，照旧走检索）。
# 注意**不收裸「订单」**：「把所有订单都列出来」（越狱注入）含订单二字，不能
# 被澄清截胡——收的必须是「查我的单子」状态形态。评审后补齐的形态：呢尾
# （订单呢/物流呢/快递呢——裸名词+呢即「我的单子呢」）、到哪里了、发货没、
# 查快递、查下X（「查下物流」的中缀）、包裹。
# 审计刀 14 收口：**裸「包裹/订单信息/物流信息」不触发**（「包裹破损怎么赔」
# 「订单信息怎么改」「物流信息错误」会被截胡去要单号）——收的必须是查单状态
# 形态；**催单族补齐**（怎么还没/还没到/没收到/没到货——审计实测「东西怎么
# 还没到」此前落检索引评论）。
# 「物流信息$|订单信息$」：裸名词收尾（「物流信息」=查我的物流）才触发；
# 后接其他词（物流信息错误/如何查询/怎么改）是别的问题，不截胡。
_ORDER_STATE_CLARIFY_RE = re.compile(
    "查物流|查下物流|物流单号|快递单号|物流信息$|订单信息$|到哪了|到哪儿了|到哪里了|"
    "到货了吗|到货了么|发货了吗|发货了没|发货没|查订单|查下订单|查询订单|"
    "订单查询|订单到哪|查快递|查下快递|(订单|物流|快递|包裹)呢|"
    "包裹到哪|包裹到哪了|包裹到哪里|怎么还没|咋还没|还没到|没收到|没到货"
)
_ORDER_CLARIFY_MAX_CHARS = 14
# 混意图排除（评审 P1-2）：问句里还有第二个诉求（政策/退货/保修/优惠…）或
# 分句标点时，整句留给提议步综合（ADR 0043 的「订单到哪了顺便问下退货政策」
# 正是它的教科书例）——澄清不能吞掉第二意图。
# 只用**词**信号不用标点——单句问号（「到货了吗？」）不是混意图，词才是第二诉求
_ORDER_CLARIFY_MIXED_RE = re.compile(
    "政策|退货|退换|保修|发票|优惠|顺便|还有|以及|另外|然后|同时|怎么办|怎么开|怎么改|怎么填|错误"
)
_ORDER_CLARIFY_CONTENT = "请提供订单号（SO- 开头），我帮你查询订单状态。"


def _run_order_clarify_ask(db: Session, session: ServiceSession, question: str) -> AskOutcome:
    """无单号的订单状态问 -> 请求订单号（第 70 刀，ADR 0036 修订）。

    kind=answer 的**澄清**而非拒答/转人工：缺的不是知识（缺口）也不是人（工单），
    是**输入**——顾客补单号后下一问即走订单工具（自然多轮闭环，引擎金标
    eg-mt-002 钉）。工具条留 ``need_order_no`` 伪工具记录（与订单/库存工具同为
    「已发生的动作留档」，回放可见引擎做了什么）。
    """
    tool_record = {"name": "need_order_no", "arg": "-", "result": "已请求订单号"}
    # 答上即关（审计刀 11 契约的泛化口径）：澄清也是一种「当下已有应答」——
    # 同问的 open 缺口一并收掉，治理台「待补」不挂「缺订单号」这种输入问题。
    resolve_gap_answered_by_catalog(db, question)
    agent_message = ServiceMessage(
        session_id=session.id,
        role="agent",
        content=_ORDER_CLARIFY_CONTENT,
        citations=[],
        kind="answer",
        handoff=False,
        tool=tool_record,
    )
    db.add(agent_message)
    db.commit()
    db.refresh(agent_message)
    return AskOutcome(
        agent_message=agent_message,
        answer=ComposedAnswer(
            content=_ORDER_CLARIFY_CONTENT, citations=[], kind="answer", handoff=False
        ),
        gap=None,  # 澄清不是知识缺口（去补=顾客补单号，不是补文档）
        generated=False,
        fallback=False,  # 非降级：请求输入是正式产出（同工具路径口径，0036）
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
        # 订单工具路径只有单号（没有原问句入参）——不在这里收缺口：订单问句要么
        # 查到（工具事实）要么转人工，落缺口的场景不存在（审计刀 12 记）。
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
    # 查无/故障走 handoff -> 建/取工单（第 42 刀补口，answer 分支恒 None）
    ticket = _ensure_handoff_ticket(db, session, agent_message, handoff)
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
        ticket=ticket,
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
    # 类目聚合（第 56 刀）：结果里带 category 就算事实分支（它没有单件 stock 字段——
    # 「你们有笔记本吗」问的是一类有没有货，件数/有货数是事实，不是「未设置」）
    is_category = "category" in result
    tool_record = {
        "name": "get_stock",
        "arg": result["product_name"] if found else question,
        "result": summarize_stock_result(result),
    }
    if found and (is_category or stock is not None):
        kind, handoff = "answer", False
        content = render_stock_answer(result)
        resolve_gap_answered_by_catalog(db, question)  # 审计刀 12：答上即关同问缺口
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
    # 库存 NULL/故障/无商品走 handoff -> 建/取工单（第 42 刀补口）
    ticket = _ensure_handoff_ticket(db, session, agent_message, handoff)
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
        ticket=ticket,
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

    第 42 刀（ADR 0046 §4）：handoff/拒答路径 outcome.ticket 非 None ->
    complete 追加运行时可选键 ticket_id/ticket_no/ticket_contact_at（H 号是
    给顾客的回执，**不走 expose_gap_id 白名单**——缺口号是内部 ID；两条通道
    同形状）。工具条 name="handoff" 的 thinking 换「正在转接人工…」。
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
                # 第 42 刀：转人工（ADR 0046，建/取工单+回执）
                "handoff": HANDOFF_THINKING_TEXT,
                # 第 70 刀：无单号澄清（没发生订单查询，不装作查过）
                "need_order_no": CLARIFY_THINKING_TEXT,
            }.get(str(outcome.tool.get("name")), ORDER_THINKING_TEXT)
        yield sse_event("thinking", {"text": thinking_text})
        # 顾客通道剥写动作凭证（审计刀 8 P1）；操作者通道原样（确认端点要用）
        yield sse_event("tool", outcome.tool if expose_gap_id else customer_safe_tool(outcome.tool))
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
        # 既有消费方按键取值不受影响；通道差异只在写凭证（见 customer_safe_tool）
        "tool": outcome.tool if expose_gap_id else customer_safe_tool(outcome.tool),
    }
    if expose_gap_id:
        # 拒答=缺口 id（前端芯片跳治理台缺口 tab）；answer 恒为 null
        complete["gap_id"] = outcome.gap.id if outcome.gap is not None else None
    # 第 7 刀：true=厂商生成失败降级模板（前端「模板回退」徽章；运行时返回，
    # 同 gap_id 口径，消息表不加列）
    complete["fallback"] = outcome.fallback
    if outcome.fallback_reason is not None and expose_gap_id:
        # 第 40 刀：忠实度闸触发原因（可观测可校准；普通厂商失败降级不带
        # 该键——运行时返回口径同 fallback，消息表不加列）。
        # **只给操作者通道**（审计刀 12 P2）：它是内部闸口径（coverage/no_coverage），
        # 与 gap_id 同一白名单思路——顾客面只需要 fallback 布尔（徽章），不需要
        # 知道我们内部哪道闸触发（口径不一致曾被审计记为回归面）。
        complete["fallback_reason"] = outcome.fallback_reason
    if outcome.ticket is not None:
        # 第 42 刀（ADR 0046 §4/裁决 8）：H 号是给顾客的回执——运行时可选键，
        # **不走 expose_gap_id 白名单**（缺口号是内部 ID，工单号是顾客的）；
        # 两条通道（顾客/操作者）同形状。ticket_contact_at 让前端在重载后由
        # 后端 contact_at 决定表单状态（NULL=还没留）。
        complete["ticket_id"] = outcome.ticket.id
        complete["ticket_no"] = ticket_no(outcome.ticket)
        complete["ticket_contact_at"] = (
            outcome.ticket.contact_at.isoformat() if outcome.ticket.contact_at is not None else None
        )
    yield sse_event("complete", complete)
