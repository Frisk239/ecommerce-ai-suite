"""退货资格工具单测（第 40 刀，ADR 0044 §一/§二，无 DB）：

- 意图判定（has_return_intent）：发起退货 vs 问退货进度（后者走订单工具）。
- 窗期判定（is_within_window）：15 天内 eligible、边界、超窗。
- 确认令牌（make_token/verify_token）：签验回路、篡改、过期、资格翻转、
  env 密钥隔离（monkeypatch RETURN_TOKEN_SECRET 后与缺省派生互不相验）。
- 摘要与回答模板（summarize/render）：token 只进工具条文案，顾客文本不带。
- 覆盖度（coverage_ratio，检索模块）：高分/低分/纯停用词——忠实度闸的纯函数侧。
- 注册表接线：check_return_eligibility 可提议（参数白名单/归一）、
  create_return 不在注册表（越狱提议被拒，授权在代码）。
"""

from datetime import UTC, datetime, timedelta

import pytest

from suite_api.services import return_tools
from suite_api.services.agent_tools import TOOL_REGISTRY, parse_tool_proposal
from suite_api.services.retrieval import coverage_ratio

_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


# ---------- 意图判定 ----------


def test_return_intent_matches_refund_request() -> None:
    assert return_tools.has_return_intent("SO-1001 我想退货")
    assert return_tools.has_return_intent("这个我要退换了")


def test_return_intent_rejects_progress_questions() -> None:
    # 问进度/轨迹不是发起退货——回订单工具看事件时间轴（确认后的退货事件由此可见）
    assert not return_tools.has_return_intent("SO-1001 退货进度")
    assert not return_tools.has_return_intent("帮查退货物流轨迹")
    assert not return_tools.has_return_intent("保温杯有货吗")


# ---------- 窗期判定 ----------


def test_window_eligible_within_15_days() -> None:
    placed = _NOW - timedelta(days=5)
    assert return_tools.is_within_window(placed, now=_NOW) is True


def test_window_boundary_15_days_exactly() -> None:
    placed = _NOW - timedelta(days=15)
    assert return_tools.is_within_window(placed, now=_NOW) is True


def test_window_ineligible_beyond_15_days() -> None:
    placed = _NOW - timedelta(days=15, seconds=1)
    assert return_tools.is_within_window(placed, now=_NOW) is False
    assert return_tools.is_within_window(_NOW - timedelta(days=30), now=_NOW) is False


# ---------- 确认令牌：签验四态 ----------


@pytest.fixture(autouse=True)
def _isolated_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # 密钥 env 隔离：本模块全部签验用同一显式密钥，缺省派生路径单测见下
    monkeypatch.setenv("RETURN_TOKEN_SECRET", "unit-test-secret")


def test_token_roundtrip_verifies() -> None:
    token = return_tools.make_token("SO-1001", True, now=_NOW)
    assert return_tools.verify_token(token, "SO-1001", True, now=_NOW) is True


def test_token_rejects_tampered_signature() -> None:
    token = return_tools.make_token("SO-1001", True, now=_NOW)
    tampered = ("0" if token[0] != "0" else "1") + token[1:]
    assert return_tools.verify_token(tampered, "SO-1001", True, now=_NOW) is False


def test_token_rejects_expired() -> None:
    issued_at = _NOW - timedelta(seconds=return_tools.TOKEN_TTL_SECONDS + 1)
    token = return_tools.make_token("SO-1001", True, now=issued_at)
    assert return_tools.verify_token(token, "SO-1001", True, now=_NOW) is False
    # 有效期内但换到过期后验证同样拒
    fresh = return_tools.make_token("SO-1001", True, now=_NOW)
    later = _NOW + timedelta(seconds=return_tools.TOKEN_TTL_SECONDS + 1)
    assert return_tools.verify_token(fresh, "SO-1001", True, now=later) is False


def test_token_binds_order_no_and_eligibility() -> None:
    token = return_tools.make_token("SO-1001", True, now=_NOW)
    # 单号不符（把 token 张冠李戴）与资格翻转都验不过——资格哈希绑定
    assert return_tools.verify_token(token, "SO-1002", True, now=_NOW) is False
    assert return_tools.verify_token(token, "SO-1001", False, now=_NOW) is False


def test_token_rejects_garbage_shapes() -> None:
    assert return_tools.verify_token("", "SO-1001", True) is False
    assert return_tools.verify_token("short", "SO-1001", True) is False
    assert return_tools.verify_token("a" * 32 + "not-a-number", "SO-1001", True) is False


def test_secret_prefers_env_over_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETURN_TOKEN_SECRET", "env-secret")
    env_based = return_tools.return_secret()
    assert env_based == b"env-secret"
    monkeypatch.delenv("RETURN_TOKEN_SECRET")
    derived = return_tools.return_secret()
    assert derived != env_based
    # 密钥不同 -> 令牌互不相验（跨部署密钥轮换语义）
    token = return_tools.make_token("SO-1001", True, now=_NOW)
    monkeypatch.setenv("RETURN_TOKEN_SECRET", "env-secret")
    assert return_tools.verify_token(token, "SO-1001", True, now=_NOW) is False


# ---------- 摘要与回答模板 ----------


def test_summarize_eligible_carries_confirmation_token() -> None:
    token = return_tools.make_token("SO-1001", True, now=_NOW)
    text = return_tools.summarize_eligibility(
        {"found": True, "order_no": "SO-1001", "eligible": True, "confirmation_token": token}
    )
    assert "待确认" in text and token in text
    # 不可退/查无/故障态都没有 token 段（前端据此决定渲染确认按钮与否）
    assert "token" not in return_tools.summarize_eligibility(
        {"found": True, "order_no": "SO-1001", "eligible": False, "reason": "超窗"}
    )
    assert return_tools.summarize_eligibility({"found": False}) == "未找到"
    assert return_tools.summarize_eligibility({"error": True}) == "查询失败"


def test_render_answer_eligible_promises_operator_confirmation() -> None:
    text = return_tools.render_eligibility_answer(
        {"found": True, "order_no": "SO-1001", "eligible": True}
    )
    assert "SO-1001" in text
    assert "等待客服确认" in text
    assert "token" not in text  # 顾客可见文本不带令牌（令牌只进工具条/轨迹）


def test_render_answer_ineligible_states_reason() -> None:
    text = return_tools.render_eligibility_answer(
        {"found": True, "order_no": "SO-1003", "eligible": False, "reason": "超窗"}
    )
    assert "SO-1003" in text and "超窗" in text


# ---------- 忠实度闸纯函数：覆盖度 ----------


def test_coverage_full_overlap_means_normal_generation() -> None:
    assert coverage_ratio("筷长是多少？", ["筷长：26cm"]) == pytest.approx(1.0)


def test_coverage_low_overlap_under_threshold() -> None:
    # 「这个叉子能不能进洗碗机消毒」有效 bigram=6 个，命中块只共享 {叉子}
    # -> 1/6 < 0.4（闸触发态：单块薄证据不调模型）
    ratio = coverage_ratio("这个叉子能不能进洗碗机消毒", ["叉子：不锈钢"])
    assert ratio == pytest.approx(1 / 6)
    assert ratio < 0.4


def test_coverage_multi_chunk_union_counts() -> None:
    # 多块并集：每块各贡献部分查询词 -> 覆盖度抬高（闸只收单块薄证据；
    # 2/3 虽不满也高于阈值——「命中>1 或覆盖够高即正常生成」口径）
    ratio = coverage_ratio("叉子材质", ["叉子", "材质"])
    assert ratio == pytest.approx(2 / 3)
    assert ratio >= 0.4


def test_coverage_stopword_only_query_conservatively_passes() -> None:
    # 纯停用词问句根本到不了生成（retrieve 返回空 -> 拒答），闸保守不触发
    assert coverage_ratio("的了吗", ["任何块"]) == 1.0


# ---------- 注册表接线 ----------


def test_eligibility_tool_is_proposable_and_normalizes_order_no() -> None:
    decision = parse_tool_proposal('TOOL: check_return_eligibility {"order_no": "so-1001"}')
    assert decision.reject_reason is None
    assert decision.proposal is not None
    assert decision.proposal.name == "check_return_eligibility"
    assert decision.proposal.args == {"order_no": "SO-1001"}


def test_eligibility_tool_rejects_bad_args() -> None:
    decision = parse_tool_proposal('TOOL: check_return_eligibility {"order_no": "所有订单"}')
    assert decision.proposal is None
    assert decision.reject_reason is not None


def test_create_return_is_not_in_registry() -> None:
    # 越狱形态（ADR 0044：写工具不在模型可提议集）——模型提议 create_return
    # 直接被注册表拒（转人工），到不了任何执行层
    assert "create_return" not in TOOL_REGISTRY
    decision = parse_tool_proposal('TOOL: create_return {"order_no": "SO-1001"}')
    assert decision.proposal is None
    assert decision.raw_name == "create_return"
    assert "create_return" in (decision.reject_reason or "")
