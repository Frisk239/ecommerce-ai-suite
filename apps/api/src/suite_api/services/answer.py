"""回答组装（第一版=证据组装模板，ADR 0023 工程选型；无 LLM 调用）。

规则（任务锁定）：
- 命中规格文档块（或其字段块）->「根据已发布的规格文档《标题》：」+ 证据句；
  命中块本身是「字段：值」行时，字段值句与证据句同源（字段块就是证据），
  直接陈述不复读。
- 命中对话块 ->「根据已发布的客服对话记录：」+ 证据句。
- 多源命中取分数最高 1-2 条，各带引用（0007：引用=资产 ID+版本号）。
- 无命中 -> kind=refusal，固定文案 + handoff=True（0018：拒答是消息种类不是
  异常；不编造、不降级闲聊、显性转人工）。
"""

import re
from dataclasses import dataclass
from typing import Any

from suite_api.services.retrieval import FIELD_LINE_RE  # 同口径复用「字段：值」识别

REFUSAL_CONTENT = "抱歉，已发布资产里没有能回答这个问题的证据。"

_MAX_EVIDENCE = 2  # 多源命中最多引 1-2 条（宁少而准，0018 宁缺勿滥）


@dataclass(frozen=True)
class ComposedAnswer:
    content: str
    citations: list[dict[str, Any]]
    kind: str  # "answer" | "refusal"
    handoff: bool


def _document_sentence(title: str, chunk: str) -> str:
    field_match = re.match(r"^(\S{1,12}?)\s*[:：]\s*(.+)$", chunk) if FIELD_LINE_RE.match(chunk) else None
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
