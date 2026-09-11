"""工具注册表（第 37 刀「客服真 loop」，ADR 0043：模型提议、代码授权）。

- ``TOOL_REGISTRY``：白名单只读工具（get_order_status/get_stock）——描述、
  参数 schema、校验器、执行函数四件套集中一处；执行函数复用 order_tools/
  stock_tools 既有实现（不重写，0036/0037 语义零改动）。**authority lives
  in code**：模型只能提议注册表内工具、只能填白名单参数，参数不合法即拒绝
  转人工——提示注入改不了工具资格。
- ``propose_prompt(question, history)``：提议步双 prompt——system 含三工具
  描述与 ``TOOL: 工具名 {"参数": "值"}`` 单行输出约定（格式化约定而非
  function calling API：厂商网关零特殊依赖，ADR 0043 动机段）与「无需工具
  则直接回答」指引；第 42 刀加 handoff sentinel 选项（顾客明确要人/投诉/
  举报时 ``TOOL: handoff {}``，不属于注册表）；顾客问句过 redact（0038
  纪律：进厂商 prompt 必掩）。
- ``parse_tool_proposal(llm_text)`` -> ``ToolDecision``：正则抓提议标记，
  三态——合法（``proposal`` 非 None）、被拒（``reject_reason`` 非 None：
  坏 JSON/未注册/参数不合法/格式坏）、纯文本（两者皆 None=模型认为无需
  工具，调用方走检索步）；第 42 刀多一态 ``handoff``（sentinel 在查
  registry 之前识别，见函数注释）。被拒时带 ``raw_name/raw_args`` 轨迹字段
  供消息落库回放。
- ``check_return_eligibility``（第 40 刀，ADR 0044 §一）：第三个可提议工具，
  阶段一资格查询（只读：窗期判定在代码 + eligible 时签发 HMAC 确认令牌）；
  **create_return 不进注册表**——写工具不在模型可提议集，创建只能由操作者
  确认端点携 token 执行（services/return_tools）。
- Out（spec）：function calling API、新表。
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from suite_api.services.machine_wash import redact
from suite_api.services.order_tools import get_order_status
from suite_api.services.return_tools import check_return_eligibility

logger = logging.getLogger(__name__)

# 提议标记约定（ADR 0043）：单行 ``TOOL: 工具名 {"参数": "值"}``。IGNORECASE
# 宽进（模型大小写不稳），校验从严（参数白名单+格式）；非贪婪抓 JSON——参数
# 均为扁平字符串对象，嵌套结构截断后 JSON 解析失败=拒绝（保守方向）。
TOOL_MARKER_RE = re.compile(r"TOOL:\s*(\w+)\s*(\{.*?\})", re.IGNORECASE | re.DOTALL)
# 含 TOOL 字样但不匹配约定 -> 格式坏（模型想调工具但输出不合规：拒绝转人工，
# 不当纯文本放行——放行会把「想查单没查成」伪装成已回答）。
_TOOL_MENTION_RE = re.compile(r"\bTOOL\b", re.IGNORECASE)

PROPOSE_SYSTEM_PROMPT = (
    "你是商家侧电商 AI 客服的工具分派器，根据顾客问题决定是否调用工具。\n"
    "可用工具只有三个：\n"
    "- get_order_status：查询订单状态与物流轨迹。参数 order_no 必填，"
    "格式为 SO-数字（如 SO-1001）。一次只能查一个订单。\n"
    "- get_stock：查询商品是否有货。参数 product_name 可选（商品名）。\n"
    "顾客明确要求人工客服、投诉或举报时，转人工：只输出一行 TOOL: handoff {}\n"
    '需要调用工具时，只输出一行：TOOL: 工具名 {"参数名": "参数值"}\n'
    "无需工具（政策、售后、闲聊等）时，直接输出给顾客的回答文本，不要输出 TOOL。\n"
    "除上述三个工具与 handoff 外不要提议任何操作；不能列出订单清单、不能导出、不能修改数据。"
)


def propose_prompt(question: str, history: list[dict[str, str]] | None = None) -> tuple[str, str]:
    """组装提议步 (system, user) prompt。history（recent_turns 产出，已掩）
    渲染为对话段帮模型消解代词指代（单号常在上一问）；顾客问题过 redact
    （0038：本模块是厂商 prompt 边界，收口在此，调用方不重复掩）。"""
    lines: list[str] = []
    for turn in history or []:
        speaker = "顾客" if turn["role"] == "customer" else "客服"
        lines.append(f"{speaker}：{turn['content']}")
    if lines:
        lines.append("")
    lines.append(f"顾客问题：{redact(question)}")
    return PROPOSE_SYSTEM_PROMPT, "\n".join(lines)


@dataclass(frozen=True)
class ToolProposal:
    """一个通过校验的合法提议：注册表内工具 + 白名单内规范化参数。"""

    name: str
    args: dict[str, str]


@dataclass(frozen=True)
class ToolDecision:
    """提议步裁决三态：合法（proposal 非 None）/ 被拒（reject_reason 非
    None，raw_* 供轨迹落库）/ 纯文本（两者皆 None=无需工具走检索步）。

    第 42 刀（ADR 0046 §2）加 ``handoff``：模型按提示输出 ``TOOL: handoff {}``
    时置真——handoff 是 sentinel 不是注册表工具（0043「三个只读工具」契约
    不破）。三态之上多一个转人工态，消费方先判 handoff 再判其余。"""

    proposal: ToolProposal | None = None
    reject_reason: str | None = None
    raw_name: str | None = None
    raw_args: str | None = None
    handoff: bool = False


@dataclass(frozen=True)
class ToolSpec:
    """注册表条目：描述（进提议 prompt）、参数白名单、必填集、格式正则、
    执行函数（复用既有工具实现，签名 (db, args, question)）。"""

    name: str
    description: str
    params: dict[str, str]
    required: tuple[str, ...]
    arg_patterns: dict[str, re.Pattern[str]]
    run: Callable[[Session, dict[str, str], str], dict[str, Any]]


def _validate_args(
    spec: ToolSpec, args: dict[str, Any]
) -> tuple[dict[str, str] | None, str | None]:
    """白名单校验：键必须在 params 内（多余键拒绝——「查所有订单」类越狱
    载荷进不来）、值必须是字符串且匹配格式正则、必填键必须在场。返回
    (规范化参数, None) 或 (None, 拒绝原因)。"""
    cleaned: dict[str, str] = {}
    for key, value in args.items():
        if key not in spec.params:
            return None, f"参数 {key} 不在工具参数白名单内"
        if not isinstance(value, str):
            return None, f"参数 {key} 必须是字符串"
        pattern = spec.arg_patterns.get(key)
        if pattern is not None and pattern.fullmatch(value) is None:
            return None, f"参数 {key} 不合法：{value}"
        # order_no 归一大写（与 find_order_no 同口径：seed/查询按大写存取）
        cleaned[key] = value.upper() if key == "order_no" else value
    for key in spec.required:
        if key not in cleaned:
            return None, f"缺少必填参数：{key}"
    return cleaned, None


def parse_tool_proposal(llm_text: str) -> ToolDecision:
    """解析提议步 LLM 输出为 ToolDecision（三态语义见类注释）。解析失败/
    未注册/参数不合法一律拒绝（reason 非 None）——模型提议、代码授权。"""
    text = (llm_text or "").strip()
    match = TOOL_MARKER_RE.search(text)
    if match is None:
        if _TOOL_MENTION_RE.search(text):
            return ToolDecision(
                reject_reason="提议格式无法解析",
                raw_args=text[:200] or None,
            )
        return ToolDecision()  # 纯文本：模型认为无需工具
    raw_name, raw_args = match.group(1), match.group(2)
    # 第 42 刀（ADR 0046 §2）：handoff 是 sentinel 不是注册表工具——必须在查
    # registry 之前识别，否则会落「未注册工具」被拒（文案变 REJECTED_CONTENT，
    # 语义全错）。注册表保持「三个只读工具」精确集合契约（不把 handoff 注册进去）。
    if raw_name.lower() == "handoff":
        return ToolDecision(handoff=True)
    spec = TOOL_REGISTRY.get(raw_name)
    if spec is None:
        return ToolDecision(
            reject_reason=f"未注册工具：{raw_name}", raw_name=raw_name, raw_args=raw_args
        )
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        return ToolDecision(
            reject_reason="提议参数不是合法 JSON", raw_name=raw_name, raw_args=raw_args
        )
    if not isinstance(args, dict):
        return ToolDecision(
            reject_reason="提议参数必须是 JSON 对象", raw_name=raw_name, raw_args=raw_args
        )
    cleaned, reason = _validate_args(spec, args)
    if reason is not None:
        return ToolDecision(reject_reason=reason, raw_name=raw_name, raw_args=raw_args)
    return ToolDecision(proposal=ToolProposal(name=raw_name, args=cleaned))


def execute_tool(db: Session, proposal: ToolProposal, question: str) -> dict[str, Any]:
    """代码授权后的执行入口：查表分发到既有只读实现（order/stock 工具零
    重写）。question 用于 get_stock 无商品名提议时按原问句做 LCS 商品匹配
    （与快路径 query_stock 口径一致）。"""
    spec = TOOL_REGISTRY[proposal.name]
    return spec.run(db, proposal.args, question)


def _run_get_order_status(db: Session, args: dict[str, str], _question: str) -> dict[str, Any]:
    return get_order_status(db, args["order_no"])


def _run_get_stock(db: Session, args: dict[str, str], question: str) -> dict[str, Any]:
    """get_stock 的提议步执行（审计刀 13 B 轴 P2 修订）。

    匹配仍用 LLM 抽取的 ``product_name``（比原问句干净、LCS 更准），但**纯度闸
    按原问句判**：闸的契约是问句文本，拿干净商品名去判残差必然为空，闸形同虚设
    ——模型对「保温杯可以刻字吗」提议 get_stock(保温杯) 时，工具条会亮
    「有货 · 42 件」误导、上下文被污染。命中后回剔原问句，剔不干净就按未找到
    处理（提议步不硬答，只影响工具条与生成上下文）。
    """
    from suite_api.services.stock_tools import query_stock, stock_product_residual, stock_residual

    name = args.get("product_name")
    if not name:
        return query_stock(db, question)
    result = query_stock(db, name)
    if not result.get("found"):
        return result
    matched = str(result.get("product_name") or name)
    residual = (
        stock_residual(question, name if name in question else matched)
        if "category" in result
        else stock_product_residual(question, matched)
    )
    if residual != "":
        return {"found": False}
    return result


def _run_check_return_eligibility(
    db: Session, args: dict[str, str], _question: str
) -> dict[str, Any]:
    return check_return_eligibility(db, args["order_no"])


# 注册表（ADR 0043）：v1 三个只读工具（第 40 刀加退货资格查询——写工具
# create_return 仍不在注册表，ADR 0044 §一）。get_stock 无必填参数=宽松校验
# 器——裸「有货吗」提议允许空参数，按原问句走商品匹配；白名单键外一律拒绝。
TOOL_REGISTRY: dict[str, ToolSpec] = {
    "get_order_status": ToolSpec(
        name="get_order_status",
        description="按订单号查询订单状态与物流轨迹",
        params={"order_no": "订单号，SO-数字，如 SO-1001"},
        required=("order_no",),
        arg_patterns={"order_no": re.compile(r"SO-\d+", re.IGNORECASE)},
        run=_run_get_order_status,
    ),
    "get_stock": ToolSpec(
        name="get_stock",
        description="查询商品当前库存（有货/无货/未设置）",
        params={"product_name": "商品名，可选"},
        required=(),
        arg_patterns={},
        run=_run_get_stock,
    ),
    "check_return_eligibility": ToolSpec(
        name="check_return_eligibility",
        description="查询订单是否在退货窗内、能否退货（只读资格判定，退货创建须客服确认）",
        params={"order_no": "订单号，SO-数字，如 SO-1001"},
        required=("order_no",),
        arg_patterns={"order_no": re.compile(r"SO-\d+", re.IGNORECASE)},
        run=_run_check_return_eligibility,
    ),
}
