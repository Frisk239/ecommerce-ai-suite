"""机洗：按所挂商品 spec_schema 的字段集合做结构化抽取（0009：可弃权，禁止编造）。

纯函数、无框架依赖（便于单测）。规则（任务锁定的正则口径）：

- 净含量 ``(\\d+(?:\\.\\d+)?)\\s*(ml|毫升|l|升|g|克|kg|千克)``，大小写不敏感；
- 保质期 ``(\\d+)\\s*(个月|天|日|月|年)``，"12个月" 优先整体（alternation 中
  "个月" 排在 "月" 前）；日期陷阱（生产日期/出厂日期/批号/日期 + 数字年月日）
  先预扫剔除，避免把「2026年」当保质期；
- 材质：只认显式分隔符模式「材质：X」「材质为X」「材质是X」「X材质」；
  禁止裸通配——原型第五轮教训：「未标注材质牌号」被抽成「牌号」。
  分隔式与后缀式共用同一否定词黑名单（未标注/不详…），命中即弃权；
  后缀式 X 若是引导动词（未标注/说明/采用…）或剥离动词后为空，则弃权。

字段集合按资产种类分派（第 12 刀，ADR 0035）：

- 文档类挂商品 -> 商品 spec_schema 的 keys（上面的正则注册表）；未知字段名
  （未来类目扩展）一律弃权，由人洗补填。抽取不到 -> ``{abstained: true}``，
  禁止空字符串冒充（0009）。
- 种类=对话 -> 字段集只有一个 ``qa_pairs``：LLM 从转写抽问答对草稿
  （``[{q, a}, ...]`` 结构化值）。分级：未配置模型（空 key）=降级弃权、
  对话照常推进待人洗；已配置但失败/坏输出=机洗失败（停已接入可重试）。
"""

import asyncio
import json
import re
from collections.abc import Callable, Iterable

from suite_api.services import llm
from suite_platform.storage import ObjectStorage


class MachineWashError(Exception):
    """机洗失败（如文档字节不是合法 UTF-8 文本）：资产停在已接入，可就地重试。"""


# 多字符单位排前面，避免 "500千克" 被截成 "5千克" 之类的错位匹配
_NET_CONTENT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|毫升|kg|千克|l|升|g|克)", re.IGNORECASE
)
# "个月" 必须排在 "月" 前：保质期 12 个月要整体抽出 "12个月"，不是 "12月"
_SHELF_LIFE_RE = re.compile(r"(\d+)\s*(个月|天|日|月|年)")
# 日期陷阱（保质期正则负向后行的等价实现）：「生产日期：2026年8月1日」类片段
# 会被全文 search 抽成保质期（"2026年"，甚至年月日中段的 "8月"），先剔除再匹配
_DATE_TRAP_RE = re.compile(
    r"(?:生产日期|出厂日期|生产批号|批号|日期)\s*[:：]?\s*\d+\s*年"
    r"(?:\s*\d+\s*月)?(?:\s*\d+\s*日)?"
)

# 显式分隔式：「材质：X」「材质为X」「材质是X」；X 到首个空白/标点为止
_MATERIAL_DELIMITED_RE = re.compile(
    r"材质\s*[:：为是]\s*([^\s，。,;；、()（）【】\[\]]{1,30})"
)
# 后缀式：「X材质」；X 限定汉字/字母/数字，非贪婪取最短
_MATERIAL_SUFFIX_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{1,12}?)材质")

# X 命中这些引导/否定词 = 不是显式材质声明，弃权而不是硬抽（防「未标注材质牌号」类误抽）
_MATERIAL_BLOCKLIST = {
    "标注",
    "未标注",
    "不详",
    "说明",
    "描述",
    "注明",
    "写明",
    "产品",
    "商品",
    "主要",
    "相关",
    "专用",
    "该",
    "本",
    "此",
    "其",
    "之",
    "的",
    "等",
    "各",
    "同",
}
# 后缀式的动词分隔词（「杯身采用304不锈钢材质」->「304不锈钢」；动词前常是主语）
_MATERIAL_LEADING_WORDS = ("采用", "选用", "使用", "选择")


def extract_net_content(text: str) -> str | None:
    match = _NET_CONTENT_RE.search(text)
    if not match:
        return None
    number, unit = match.group(1), match.group(2)
    return f"{number}{unit}"


def extract_shelf_life(text: str) -> str | None:
    match = _SHELF_LIFE_RE.search(_DATE_TRAP_RE.sub("", text))
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def _hits_material_blocklist(candidate: str) -> bool:
    """否定词黑名单命中：值是引导词本身，或以多字否定词开头。

    「材质：未标注材质信息」「材质为不详」同「未标注材质牌号」一样不是显式
    材质声明——弃权而非硬抽（0009 禁止编造）。单字引导词（该/本/此…）只做
    精确匹配，避免误伤以其开头的正常材质词。
    """
    if candidate in _MATERIAL_BLOCKLIST:
        return True
    return any(
        len(word) > 1 and candidate.startswith(word) for word in _MATERIAL_BLOCKLIST
    )


def _clean_material_candidate(candidate: str) -> str | None:
    """后缀式清洗：把「杯身采用304不锈钢」里的动词当分隔符，取最后一段材质词。

    多字动词做分割（动词前是主语「杯身」，不是材质）；单字系词只剥头，
    避免把材质名中段截断。清洗后为空或命中否定词黑名单 -> 弃权。
    """
    for word in _MATERIAL_LEADING_WORDS:
        if word in candidate:
            candidate = candidate.rsplit(word, 1)[1]
    for single in ("为", "是"):
        while candidate.startswith(single) and len(candidate) > 1:
            candidate = candidate[1:]
    candidate = candidate.strip()
    if not candidate or _hits_material_blocklist(candidate):
        return None
    return candidate


def extract_material(text: str) -> str | None:
    # 1) 显式分隔式优先（材质：X / 材质为X / 材质是X）；值过同一否定词黑名单，
    #    命中（「未标注」「不详」及其开头词）即弃权
    match = _MATERIAL_DELIMITED_RE.search(text)
    if match:
        candidate = match.group(1).strip()
        if not candidate or _hits_material_blocklist(candidate):
            return None
        return candidate
    # 2) 后缀式（X材质）：动词分割 + 黑名单，命不中显式声明就弃权
    match = _MATERIAL_SUFFIX_RE.search(text)
    if not match:
        return None
    return _clean_material_candidate(match.group(1))


FIELD_EXTRACTORS: dict[str, Callable[[str], str | None]] = {
    "净含量": extract_net_content,
    "保质期": extract_shelf_life,
    "材质": extract_material,
}


def extract_document_fields(text: str, field_names: Iterable[str]) -> dict[str, dict]:
    """对字段集合逐个抽取：``{value, source:"machine"}`` 或 ``{abstained: true}``。"""
    result: dict[str, dict] = {}
    for name in field_names:
        extractor = FIELD_EXTRACTORS.get(name)
        value = extractor(text) if extractor is not None else None
        if value is None:
            result[name] = {"abstained": True}
        else:
            result[name] = {"value": value, "source": "machine"}
    return result


# ---------- 对话种类：LLM 抽 QA 草稿（第 12 刀，ADR 0035） ----------

QA_FIELD = "qa_pairs"
QA_FAILURE_MESSAGE = "LLM QA 抽取失败"

_QA_SYSTEM_PROMPT = (
    "你是电商客服知识治理助手，从客服对话转写中抽取可复用的问答对。\n"
    "只依据转写内容回答顾客问过且有明确答复的问题；转写里没有的不要编造。\n"
    "问句改写为顾客口吻的通用问法，答案保留客服口径的关键信息，简洁完整。\n"
    '只输出一个 JSON 数组，形如 [{"q": "问题", "a": "回答"}]；没有可抽的问答就输出 []。'
    "不要输出 JSON 以外的任何解释文字。"
)


def build_qa_prompt(transcript: str) -> str:
    return f"客服对话转写：\n{transcript}"


def _strip_code_fence(raw: str) -> str:
    """剥离 ```/```json 围栏（模型常包一层；不包也兼容）。"""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = text[3:]
    if text.lower().startswith("json"):
        text = text[4:]
    text = text.strip()
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def parse_qa_output(raw: str) -> list[dict[str, str]]:
    """LLM 输出 -> [{q, a}, ...]：剥围栏 -> JSON -> 逐项形状校验（q/a 非空串）。

    坏 JSON / 不是数组 / 项形状不对 = 抽取失败（MachineWashError，停已接入可
    重试，ADR 0035 分级）；合法空数组不是失败（调用方按弃权处理）。
    """
    try:
        data = json.loads(_strip_code_fence(raw))
    except ValueError as exc:
        raise MachineWashError(QA_FAILURE_MESSAGE) from exc
    if not isinstance(data, list):
        raise MachineWashError(QA_FAILURE_MESSAGE)
    pairs: list[dict[str, str]] = []
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("q"), str)
            or not isinstance(item.get("a"), str)
            or not item["q"].strip()
            or not item["a"].strip()
        ):
            raise MachineWashError(QA_FAILURE_MESSAGE)
        pairs.append({"q": item["q"].strip(), "a": item["a"].strip()})
    return pairs


def extract_qa_draft(transcript: str) -> dict:
    """对话 QA 草稿：字段入口形状与文档机洗同构（value+source / abstained）。

    - LLMNotConfigured（空 key）= 降级弃权——第 3 刀「整段转写待人洗」行为保留，
      CI 空凭证环境零改动（不写 last_error）；
    - LLMUnavailable/超时/坏输出 = MachineWashError，停已接入存 last_error，
      走既有就地重试端点；
    - 合法 [] = 对话无可抽问答，同样弃权（不加 reason 分叉，ADR 0035）。

    asyncio.run：qa_pairs 只出现在 dialogue 机洗——登记/重试的唯一入口都是
    同步 def 路由（FastAPI 线程池），线程上没有运行中的事件循环，安全。
    """
    try:
        raw = asyncio.run(llm.complete_chat(_QA_SYSTEM_PROMPT, build_qa_prompt(transcript)))
    except llm.LLMNotConfigured:
        return {"abstained": True}
    except llm.LLMError as exc:
        raise MachineWashError(QA_FAILURE_MESSAGE) from exc
    pairs = parse_qa_output(raw)
    if not pairs:
        return {"abstained": True}
    return {"value": pairs, "source": "machine"}


def run_machine_wash(
    storage: ObjectStorage, object_key: str, field_names: Iterable[str]
) -> dict[str, dict]:
    """机洗入口：从对象存储读回字节（ADR 0003，字节只住对象存储）→ 文本 → 抽取。

    读回或解码失败抛 MachineWashError；抽取不到字段不算失败（0009 弃权）。
    字段集含 qa_pairs（对话种类）时走 LLM 抽取，其余字段走正则注册表。
    """
    data = storage.get_bytes(object_key)  # 键不存在会抛 FileNotFoundError，同样属机洗失败
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MachineWashError("文档字节不是合法 UTF-8 文本，无法机洗") from exc
    names = list(field_names)
    result = extract_document_fields(text, [n for n in names if n != QA_FIELD])
    if QA_FIELD in names:
        result[QA_FIELD] = extract_qa_draft(text)
    return result
