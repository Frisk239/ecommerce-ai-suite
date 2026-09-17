"""回答组装（第一版=证据组装模板，ADR 0023 工程选型；无 LLM 调用）。

规则（任务锁定）：
- 命中规格文档块（或其字段块）->「根据已发布的规格文档《标题》：」+ 证据句；
  命中块本身是「字段：值」行时，字段值句与证据句同源（字段块就是证据），
  直接陈述不复读。
- 命中对话块 ->「根据已发布的客服对话记录：」+ 证据句。
- 多源命中取分数最高 1-2 条，各带引用（0007：引用=资产 ID+版本号）。
- 无命中 -> kind=refusal，固定文案（第 108B 刀 W4：四段式模板，见
  REFUSAL_OPENING）+ handoff=True（0018：拒答是消息种类不是异常；不编造、
  不降级闲聊、显性转人工；工具化渠道由 build_refusal_handoff_content 拼装
  工单号/问句摘要/缺口段）。
"""

import re
from dataclasses import dataclass
from typing import Any

from suite_api.services.machine_wash import redact

# 第 108B 刀（W4）：拒答话术重写。旧文案「抱歉，已发布资产里没有能回答这个
# 问题的证据。」的「已发布资产」是内部术语（顾客不可理解）且无引导。四段式
# 确定性模板（**不调 LLM**：无证据场景是唯一没有证据背书的地方，文案可测、
# 无提示词注入面）：①承认边界+不编造理由 ②转人工回执（工单号由
# build_refusal_handoff_content 衔接）③顾客行动选项。
REFUSAL_OPENING = "抱歉，这个问题我暂时没有查到可靠的资料——不想随便编一个答案误导您。"
# 工单号是给顾客的回执（42 刀口径：不走 gap_id 白名单），{no} 空串=无号形态
# （compose_answer 的纯函数出口没有工单上下文，见 REFUSAL_CONTENT）。
_REFUSAL_HANDOFF_LINE = "已经为您转人工处理{no}，工作时间会在 4 小时内回复您。"
_REFUSAL_TAIL = "您也可以在下方留言，或者换个说法再问我一次。"
# 无工单上下文的通用形态（compose_answer 无命中出口用；引擎落库/流式文本由
# build_refusal_handoff_content 带真实工单号拼装）。
REFUSAL_CONTENT = "\n".join(
    [REFUSAL_OPENING, _REFUSAL_HANDOFF_LINE.format(no=""), _REFUSAL_TAIL]
)

_MAX_EVIDENCE = 2  # 多源命中最多引 1-2 条（宁少而准，0018 宁缺勿滥）

# 第 27 刀（演示收官）：拒答交接摘要的问句截断口径——与会话列表首问摘要
# （routes/service._first_question）同为 60 字 + 省略号，两处口径一致。
_SUMMARY_MAX_CHARS = 60


def build_refusal_handoff_content(
    question: str,
    gap_id: int | None = None,
    *,
    missing_entity: str | None = None,
    known_product: str | None = None,
    ticket_no: str | None = None,
) -> str:
    """拒答落库消息的完整文本（第 27 刀，词条「转人工」：交接带结构化摘要）。

    结构（第 108B 刀 W4 起）：拒答四段式模板（承认边界+不编造 / 转人工回执 /
    顾客行动选项，见 REFUSAL_OPENING）+ 换行 +「问句摘要：{question}」（先掩后
    截，对齐 0038「字节不动、出口必掩」与 _first_question 口径——顾客原问本就
    在同会话 customer 消息里裸存，摘要不新增暴露面，打码只是不主动多做一次
    原样复读）+（gap_id 非空时）换行 +「缺口：G-xxxx」（4 位补零，与治理台
    界面 ID 口径一致）。

    ``gap_id`` 是否可进文本由调用方通道白名单决定（run_ask 的 expose_gap_id）：
    顾客通道不带缺口 ID（0030 gap_id 白名单语义从 complete 载荷延伸到消息
    文本）；操作者通道带——治理台/客服页重开会话也能看见缺口号，不依赖
    只在流里出现一次的 complete 事件。

    ``ticket_no``（第 108B 刀 W4）：拒答路径**同事务**建/取本会话工单（42 刀
    ADR 0046），工单号 H-xxxx 是给顾客的回执（不走白名单，两通道同形状）——
    由 run_ask 在 ensure_session_ticket 之后回填进文本，顾客在消息正文里直接
    看到号码；无工单上下文（纯函数直调）时省略号码，其余文字一致。
    """
    masked = redact(question)
    summary = masked[:_SUMMARY_MAX_CHARS] + ("…" if len(masked) > _SUMMARY_MAX_CHARS else "")
    # 第 84 刀：实体存在性闸（OOV）命中时首行点名未收录对象——比「没有查到
    # 可靠的资料」精确（「资料里没有『雀巢咖啡』的信息」直接告诉顾客差什么）；
    # 非 OOV 路径首行仍是 REFUSAL_OPENING 常量原样。
    # 第 86 刀：OOV 两类分说（实体串过 redact——_normalize 保留数字，手机号问句
    # 可能成为实体串，0038 出口必掩）：
    # - 商品在库但资料没上架 -> 「资料还在补充中」（同时落知识缺口）
    # - 商品不在库 -> 「本店暂时没有这款」（只建工单，不落缺口）
    # 108B 刀：转人工回执行统一由模板承载（首行不再重复「已记录并转人工处理」
    # ——工单号本身就是记录的证据，重复陈述在四段式里是噪声）。
    if missing_entity:
        entity = redact(missing_entity)
        head = (
            f"「{entity}」的商品资料还在补充中。"
            if known_product
            else f"本店暂时没有「{entity}」这款商品。"
        )
    else:
        head = REFUSAL_OPENING
    handoff_line = _REFUSAL_HANDOFF_LINE.format(
        no=f"（工单 {ticket_no}）" if ticket_no else ""
    )
    parts = [head, handoff_line, _REFUSAL_TAIL, f"问句摘要：{summary}"]
    if gap_id is not None:
        parts.append(f"缺口：G-{gap_id:04d}")
    return "\n".join(parts)


# 「字段：值」证据句提取（单一正则，判定与分组一体；原先 FIELD_LINE_RE 判定 +
# 内联分组两段式双写，口径已合一）。字段名字符与切块同词表（中文/字母/数字，
# 不含空格：含空格的字段行切块仍整行成块，但组装走普通句模板，与原两段式
# 行为一致），1-12 字非贪婪；值首字符非空白、总长上限 201（对齐切块 gate）。
_FIELD_CAPTURE_RE = re.compile(r"^([\u4e00-\u9fa5A-Za-z0-9]{1,12}?)\s*[:：]\s*(\S.{0,200})$")


@dataclass(frozen=True)
class ComposedAnswer:
    content: str
    citations: list[dict[str, Any]]
    kind: str  # "answer" | "refusal" | "handoff"（订单工具查无/故障，ADR 0036）
    handoff: bool


def _document_sentence(title: str, chunk: str) -> str:
    field_match = _FIELD_CAPTURE_RE.match(chunk)
    if field_match:
        # 字段值句与证据句同源：字段块本身就是证据，一次陈述不复读
        return f"根据已发布的规格文档《{title}》，{field_match.group(1)}为{field_match.group(2)}。"
    return f"根据已发布的规格文档《{title}》：{chunk}。"


def _dialogue_sentence(chunk: str) -> str:
    return f"根据已发布的客服对话记录：{chunk}"


def compose_answer(
    hits: list[dict[str, Any]],
    assets_meta: dict[int, dict[str, Any]],
) -> ComposedAnswer:
    """由检索命中组装回答。hits=retrieve() 结果（分数降序）；assets_meta=
    {asset_id: {kind, title}}（由路由批量查当前资产行）。

    无命中 -> refusal+handoff（0018）；有命中 -> answer，citations 按
    (asset_id, version_no) 去重保序。

    0038 修订（第 21 刀，审计刀 4 P0 簇出口 1）：降级模板回答同样是顾客可见
    出口（_dialogue_sentence/_document_sentence 拼装的即转写/字段原文）——
    hit.chunk 已在 retrieve 返回处统一 redact（收口点见 services/retrieval.py
    注释），本模块不再重复掩，输入干净即输出干净。
    """
    if not hits:
        return ComposedAnswer(content=REFUSAL_CONTENT, citations=[], kind="refusal", handoff=True)

    sentences: list[str] = []
    citations: list[dict[str, Any]] = []
    for hit in hits[:_MAX_EVIDENCE]:
        meta = assets_meta.get(hit["asset_id"], {})
        title = meta.get("title") or "未命名资产"
        if meta.get("kind") == "dialogue":
            sentences.append(_dialogue_sentence(hit["chunk"]))
        else:
            # 本刀进索引的只有 document/dialogue；防御性走文档模板
            sentences.append(_document_sentence(title, hit["chunk"]))
        citation = {"asset_id": hit["asset_id"], "version_no": hit["version_no"]}
        if citation not in citations:
            citations.append(citation)
    return ComposedAnswer(
        content="\n".join(sentences), citations=citations, kind="answer", handoff=False
    )
