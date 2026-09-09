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
        if agent.kind in ("refusal", "handoff") or agent.tool is not None:
            continue  # 拒答/转人工/工具轮整轮跳过（含问句）
        turns.append((question, agent))
    return [
        {"role": role, "content": redact(content)}
        for question, agent in reversed(turns)
        for role, content in (("customer", question.content), ("agent", agent.content))
    ]


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
