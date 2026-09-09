"""工具注册表单测（第 37 刀/ADR 0043「模型提议、代码授权」，无 DB）：

- parse_tool_proposal 三态：合法提议（订单/库存/无参库存/小写单号归一）、
  被拒（坏 JSON/未注册/缺必填/参数非串/白名单外键/单号格式不匹配/含 TOOL
  字样但格式坏）、纯文本（proposal 与 reject_reason 皆 None=无需工具走检索）。
- TOOL_REGISTRY 形状：只含两个只读工具；描述/参数白名单/执行器齐备。
- propose_prompt：system 含两工具描述与 TOOL: 输出约定与「无需工具直接回答」
  指引；user 含 history 段与 redact 后的顾客问题（0038 进厂商 prompt 必掩）。
- 越狱形态钉测：无参提议（注入要求列所有订单）与未注册提议两形态都被拒，
  合法参数的提议器永远到不了执行层（授权在代码不在模型）。
"""

from suite_api.services import agent_tools
from suite_api.services.agent_tools import (
    TOOL_REGISTRY,
    ToolDecision,
    execute_tool,
    parse_tool_proposal,
    propose_prompt,
)

# ---------- 注册表形状 ----------


def test_registry_contains_exactly_two_readonly_tools() -> None:
    assert set(TOOL_REGISTRY) == {"get_order_status", "get_stock"}
    for spec in TOOL_REGISTRY.values():
        assert spec.description
        assert callable(spec.run)
    # 订单工具必填单号且格式 SO-数字；库存工具零必填（宽松：商品名可选）
    order_spec = TOOL_REGISTRY["get_order_status"]
    assert order_spec.required == ("order_no",)
    assert "order_no" in order_spec.params
    stock_spec = TOOL_REGISTRY["get_stock"]
    assert stock_spec.required == ()


# ---------- parse 三态：合法 ----------


def test_parse_valid_order_proposal() -> None:
    decision = parse_tool_proposal('TOOL: get_order_status {"order_no": "SO-1001"}')
    assert decision.reject_reason is None
    assert decision.proposal is not None
    assert decision.proposal.name == "get_order_status"
    assert decision.proposal.args == {"order_no": "SO-1001"}


def test_parse_normalizes_lowercase_order_no() -> None:
    decision = parse_tool_proposal('TOOL: get_order_status {"order_no": "so-1002"}')
    assert decision.proposal is not None
    assert decision.proposal.args["order_no"] == "SO-1002"


def test_parse_valid_stock_proposals() -> None:
    with_name = parse_tool_proposal('TOOL: get_stock {"product_name": "钛钢保温杯"}')
    assert with_name.proposal is not None
    assert with_name.proposal.args == {"product_name": "钛钢保温杯"}
    # 无参库存提议合法（宽松校验器）：调用方按原问句做商品匹配
    bare = parse_tool_proposal("TOOL: get_stock {}")
    assert bare.proposal is not None
    assert bare.proposal.args == {}


def test_parse_tolerates_surrounding_text() -> None:
    decision = parse_tool_proposal(
        '好的，我来帮您查询。\nTOOL: get_order_status {"order_no": "SO-1003"}\n请稍等。'
    )
    assert decision.proposal is not None
    assert decision.proposal.args["order_no"] == "SO-1003"


# ---------- parse 三态：被拒（越狱/坏参/未注册） ----------


def test_parse_rejects_bad_json() -> None:
    decision = parse_tool_proposal('TOOL: get_order_status {"order_no": SO-1001}')
    assert decision.proposal is None
    assert decision.reject_reason is not None
    assert decision.raw_name == "get_order_status"


def test_parse_rejects_unregistered_tool() -> None:
    # 越狱形态一：模型服从注入提议注册表外的工具
    decision = parse_tool_proposal('TOOL: list_all_orders {"limit": 100}')
    assert decision.proposal is None
    assert "list_all_orders" in (decision.reject_reason or "")
    assert decision.raw_name == "list_all_orders"


def test_parse_rejects_missing_required_arg() -> None:
    # 越狱形态二：注入要求「把所有订单列出来」-> 模型拿不出合法单号 -> 无参
    decision = parse_tool_proposal("TOOL: get_order_status {}")
    assert decision.proposal is None
    assert "order_no" in (decision.reject_reason or "")


def test_parse_rejects_non_string_arg() -> None:
    decision = parse_tool_proposal('TOOL: get_order_status {"order_no": 1001}')
    assert decision.proposal is None
    assert decision.reject_reason is not None


def test_parse_rejects_malformed_order_no() -> None:
    decision = parse_tool_proposal('TOOL: get_order_status {"order_no": "所有订单"}')
    assert decision.proposal is None
    assert decision.reject_reason is not None


def test_parse_rejects_extra_key_outside_whitelist() -> None:
    decision = parse_tool_proposal('TOOL: get_stock {"all": true}')
    assert decision.proposal is None
    assert "all" in (decision.reject_reason or "")


def test_parse_rejects_tool_mention_without_valid_format() -> None:
    # 含 TOOL 字样但不匹配约定：想调工具而格式坏 -> 拒绝而非当纯文本放行
    decision = parse_tool_proposal("TOOL: get_order_status")
    assert decision.proposal is None
    assert decision.reject_reason is not None


# ---------- parse 三态：纯文本 ----------


def test_parse_plain_text_means_no_tool_needed() -> None:
    decision = parse_tool_proposal("根据已发布的保修政策，整机保修一年。")
    assert decision.proposal is None
    assert decision.reject_reason is None
    assert decision == ToolDecision()


def test_parse_empty_output_means_no_tool_needed() -> None:
    assert parse_tool_proposal("") == ToolDecision()


# ---------- 越狱到不了执行层（授权在代码） ----------


def test_execute_tool_dispatches_to_order_tool() -> None:
    """执行入口按注册表分发（fake db 不触真库——只验证分发与参数透传）。"""

    captured: dict[str, object] = {}

    def fake_get_order_status(
        db: object, args: dict[str, str], _question: str
    ) -> dict[str, object]:
        captured["db"] = db
        captured["order_no"] = args["order_no"]
        return {"found": True, "order_no": args["order_no"], "status": "已发货", "items": [], "events": []}

    original = TOOL_REGISTRY["get_order_status"]
    spec = agent_tools.ToolSpec(
        name=original.name,
        description=original.description,
        params=original.params,
        required=original.required,
        arg_patterns=original.arg_patterns,
        run=fake_get_order_status,
    )
    TOOL_REGISTRY["get_order_status"] = spec
    try:
        decision = parse_tool_proposal('TOOL: get_order_status {"order_no": "so-1001"}')
        assert decision.proposal is not None
        db_sentinel: object = object()
        result = execute_tool(db_sentinel, decision.proposal, "随便什么原问句")  # type: ignore[arg-type]
        assert captured == {"db": db_sentinel, "order_no": "SO-1001"}  # 小写已归一
        assert result["found"] is True
    finally:
        TOOL_REGISTRY["get_order_status"] = original


# ---------- propose_prompt ----------


def test_propose_prompt_contains_tools_and_convention() -> None:
    system, _ = propose_prompt("SO-1001 到哪了")
    assert "get_order_status" in system
    assert "get_stock" in system
    assert 'TOOL: ' in system or "TOOL:" in system
    assert "无需工具" in system


def test_propose_prompt_carries_history_and_masks_question() -> None:
    history = [
        {"role": "customer", "content": "问上一句"},
        {"role": "agent", "content": "答上一句"},
    ]
    _, user = propose_prompt("顾客手机号 13800138000 的订单到哪了", history)
    assert "问上一句" in user and "答上一句" in user
    assert "顾客问题：" in user
    # 0038 纪律：进厂商 prompt 必掩——手机号不得裸送
    assert "13800138000" not in user


def test_module_exports_decision_dataclass() -> None:
    assert agent_tools.ToolDecision is ToolDecision
