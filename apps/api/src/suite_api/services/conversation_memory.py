"""会话多轮记忆（第 29 刀 feat/multi-turn：客服多轮记忆刀）。

裁决全在本刀短对齐 spec（.scratch/multi-turn/spec.md），要点：

- 记忆范围：当前会话内最近 n=4 轮（customer 问 + agent 答各算一段），只进
  生成 prompt 的 messages 历史段；不做跨会话/长期记忆/新表——读现有
  service_messages 即可（0023 表结构不动）。
- 跳过规则（整轮跳，含配对 customer 问）：拒答轮（kind=refusal）与转人工轮
  （kind=handoff）不进记忆——拒答内容对下一问无价值且可能误导；工具轮
  （agent.tool 非空，0036 订单/0037 库存）跳过——模板回答非对话语义。
- 记忆不改变检索与拒答语义：每问仍独立检索+独立判定有无证据——无证据仍
  拒答（记忆不制造证据）；唯一例外=检索词补全（``retrieval_query``）：本问
  含代词且历史有上一问 -> 上一问+本问拼接检索（bigram 并集，阈值不变）。
- 0038 修订纪律：历史内容进厂商 prompt 前过 redact——收口在 recent_turns
  返回处，消费方（llm.stream_chat 的 messages）不再重复掩。
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import ServiceMessage
from suite_api.services.machine_wash import redact

# 记忆窗口：最近 N 轮。spec Out：N 不做可配置——写死即裁决。
DEFAULT_TURN_WINDOW = 4

# 整轮跳过的 agent kind（记忆与回流转写共用同一规则；第 108B 刀债务落点见
# backflow_excluded_ids）。
SKIP_TURN_KINDS = ("refusal", "handoff")

# 代词正则（spec Must 3 词表：它|他|她|这个|那个|这款）。命中且历史有上一问
# 才拼接检索词；纯代词问句（无历史）不拼接，照常独立检索。
PRONOUN_RE = re.compile(r"它|他|她|这个|那个|这款")


def recent_turns(
    db: Session,
    session_id: int,
    exclude_message_id: int | None = None,
    n: int = DEFAULT_TURN_WINDOW,
) -> list[dict[str, str]]:
    """该会话最近 n 轮对话（正序，最旧在前）：``[{role, content}]``。

    - ``exclude_message_id``：排除本轮刚落的 customer 问（它不是历史）。
    - 轮 = customer 问 + agent 答按 created_at 倒序配对；拒答/转人工/工具轮
      整轮跳过（跳过=连问句一起跳，spec 裁决）。
    - 未获回答的 customer 残问（agent 组装失败的可接受残留，见 chat_engine
      模块注释）不成轮、不进记忆。
    - 内容过 redact 后返回（0038：进厂商 prompt 必掩）。
    """
    stmt = select(ServiceMessage).where(ServiceMessage.session_id == session_id)
    if exclude_message_id is not None:
        stmt = stmt.where(ServiceMessage.id != exclude_message_id)
    rows = list(
        db.scalars(stmt.order_by(ServiceMessage.created_at.desc(), ServiceMessage.id.desc()))
    )
    turns: list[tuple[ServiceMessage, ServiceMessage]] = []  # 倒序收集（最新在前）
    i = 0
    while i < len(rows) and len(turns) < n:
        agent = rows[i]
        i += 1
        if agent.role != "agent":
            continue  # 未获回答的 customer 残问：不成轮
        if i < len(rows) and rows[i].role == "customer":
            question = rows[i]
            i += 1
        else:
            continue  # agent 消息无配对问句（防御）：不成轮
        if agent.kind in SKIP_TURN_KINDS or agent.tool is not None:
            continue  # 拒答/转人工/工具轮整轮跳过（含问句）
        turns.append((question, agent))
    return [
        {"role": role, "content": redact(content)}
        for question, agent in reversed(turns)
        for role, content in (("customer", question.content), ("agent", agent.content))
    ]


def backflow_excluded_ids(messages: list[ServiceMessage] | tuple[ServiceMessage, ...]) -> set[int]:
    """回流转写排除集（第 108B 刀债务，第 109 刀修）：拒答/转人工轮**整轮**排除。

    为什么：W4 拒答四段式（不编造理由+工单号+时限+行动选项）是模板话术，回流
    登记后成为检索语料的一部分——顾客再问相近问句时 bigram 会把模板句拉成弱
    命中（错误证据面）。规则与 ``recent_turns`` 的记忆跳过**同源**（拒答/转人工
    整轮跳，含配对的 customer 问句）；工具轮在本面保留（工具答案是实质对话，
    不是记忆面的「非对话语义」问题——回流转写的价值就在真实问答链）。

    纯函数吃按 created_at 正序的消息序列，返回应排除的 message id 集合；
    agent 无配对问句时只排 agent 自己（防御）。整会话只有拒答/转人工轮时不排
    顾客问句（兜底见 ``backflow_transcript_messages``）。
    """
    excluded: set[int] = set()
    pending_customer: ServiceMessage | None = None
    for message in messages:
        if message.role == "customer":
            pending_customer = message
            continue
        if message.role != "agent":
            continue
        question, pending_customer = pending_customer, None
        if message.kind in SKIP_TURN_KINDS:
            excluded.add(message.id)
            if question is not None:
                excluded.add(question.id)
    return excluded


def backflow_transcript_messages(
    messages: tuple[ServiceMessage, ...] | list[ServiceMessage],
) -> list[ServiceMessage]:
    """回流转写最终消息列表（登记端点的唯一入口；第 108B 刀债务的落点）。

    主规则=整轮排除拒答/转人工轮（``backflow_excluded_ids``）；**兜底**：排除后
    为空（整会话只有拒答/转人工轮）时只排 agent 侧的拒答/转人工消息、保留顾客
    问句——两个理由：① 转写是登记的字节载体，空转写没有可登记的内容；② 弱命中
    噪音源在 W4**客服话术**（工单号/时限/免责句），顾客问句不是噪音源，保留它
    让「纯拒答会话」仍能回流为「未答问句」记录（治理可见），而不是把整条会话
    挡在登记之外（实施中发现 7 个测试模块 34 处依赖旧契约——422 是比债务更大
    的行为变化，故收在兜底，见 109 报告）。
    """
    excluded = backflow_excluded_ids(messages)
    kept = [message for message in messages if message.id not in excluded]
    if kept:
        return kept
    agent_side = {
        message.id
        for message in messages
        if message.role == "agent" and message.kind in SKIP_TURN_KINDS
    }
    return [message for message in messages if message.id not in agent_side]


def retrieval_query(question: str, history: list[dict[str, str]]) -> str:
    """检索词补全（spec Must 3）：本问含代词且历史有上一问 -> 上一问 + 本问。

    拼接检索=两问 bigram 并集（retrieve 打分口径与阈值不变）；无代词或无
    历史（含历史里没有 customer 问的防御分支）原样返回。history 正序，取
    末位 customer 问即「上一问」。
    """
    if not PRONOUN_RE.search(question):
        return question
    prev_questions = [turn["content"] for turn in history if turn["role"] == "customer"]
    if not prev_questions:
        return question
    return f"{prev_questions[-1]} {question}"


def last_customer_question(
    db: Session, session_id: int, exclude_message_id: int | None = None
) -> str | None:
    """本会话**上一条顾客消息**原文（第 108B 刀 W3：元问题回声的数据源）。

    与会话元意图快路径配套：顾客问「我的上一个问题是什么」这类**关于对话
    本身**的问题时，回声的是真实历史而不是模型猜测。``exclude_message_id``
    排除本轮刚落的问句（同 recent_turns 口径——它已经 commit，按 id 排除比
    按时间戳可靠）；没有任何历史（首问）返回 None，由调用方走首问边界文案。
    倒序取最近一条（created_at + id 双键，同 recent_turns 的排序口径）。
    不做 redact——掩码由消费方按出口纪律决定（元回声走 0038 出口必掩）。
    """
    stmt = select(ServiceMessage).where(
        ServiceMessage.session_id == session_id,
        ServiceMessage.role == "customer",
    )
    if exclude_message_id is not None:
        stmt = stmt.where(ServiceMessage.id != exclude_message_id)
    row = db.scalar(
        stmt.order_by(ServiceMessage.created_at.desc(), ServiceMessage.id.desc()).limit(1)
    )
    return row.content if row is not None else None


def last_tool_subject(db: Session, session_id: int) -> str | None:
    """最近一次**已答工具轮**的命中对象（第 73 刀）：供省略追问复用。查询限 agent 轮，本轮 customer 消息天然不在其中。

    「钛钢保温杯有货吗」→「还有吗」：bare 追问无主语也无代词，检索词拼接
    （retrieval_query）帮不上——但上一轮 agent 消息的 ``tool`` 列记着
    ``{name: get_stock, arg: 钛钢保温杯}``，arg 就是现成的对象记录。
    只认 **kind=answer 的工具轮**（catalog/get_stock）：拒答/转人工轮没有
    「已答」的对象可复用；``need_order_no`` 的 arg 是 "-"（第 70 刀伪工具），
    显式跳过。倒序找最近一条，没有则 None。
    """
    stmt = select(ServiceMessage).where(
        ServiceMessage.session_id == session_id,
        ServiceMessage.role == "agent",
        ServiceMessage.kind == "answer",
        ServiceMessage.tool.isnot(None),
    )
    rows = list(
        db.scalars(stmt.order_by(ServiceMessage.created_at.desc(), ServiceMessage.id.desc()))
    )
    for row in rows:
        tool = row.tool or {}
        name = str(tool.get("name") or "")
        arg = tool.get("arg")
        if name in {"catalog", "get_stock"} and isinstance(arg, str) and arg.strip() not in {"", "-"}:
            return arg
    return None
