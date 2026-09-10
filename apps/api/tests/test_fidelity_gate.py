"""忠实度闸单测（第 40 刀，ADR 0044 §二：覆盖不足不生成，无 DB）：

- 擦边问句（单命中且覆盖 < 0.4）-> 降级模板回答（不调模型：stream_chat 替身
  被触即炸），fallback=True 且 fallback_reason="coverage"。
- 正常问句（覆盖 >= 0.4）照旧调模型生成。
- 双命中（命中>1）不受闸影响——闸只收单块薄证据。
- 零命中照旧拒答（0018 不动），闸不参与拒答路径。
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from suite_api.services.chat_engine import run_ask


def _hit(asset_id: int, chunk: str) -> dict[str, Any]:
    return {"asset_id": asset_id, "version_no": 1, "chunk": chunk, "score": 0.7}


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """run_ask 的替身环境：检索/组装可注入，stream_chat 调用被记账（触发即
    失败的哨兵形态见各用例参数）。"""
    calls: dict[str, Any] = {"stream_called": 0, "prompt": None, "gap_called": 0}
    db = MagicMock()
    session = MagicMock()
    session.id = 1

    def fake_stream(_system: str, user: str, history: list[dict[str, str]] | None = None) -> Any:
        calls["stream_called"] += 1
        calls["prompt"] = user

        async def _gen() -> Any:
            yield "模型回答"

        return _gen()

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", fake_stream)
    monkeypatch.setattr("suite_api.services.chat_engine.recent_turns", lambda *_a, **_k: [])
    return {"db": db, "session": session, "calls": calls}


def test_low_coverage_single_hit_falls_back_without_llm(
    engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 擦边问句：6 个有效 bigram 只共享 1 个 -> 覆盖 1/6 < 0.4，单命中 -> 闸触发
    hits = [_hit(3, "叉子：不锈钢")]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {3: {"kind": "document", "title": "叉子说明"}},
    )

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "这个叉子能不能进洗碗机消毒"))

    assert engine["calls"]["stream_called"] == 0  # 不调模型（覆盖不足大概率编造）
    assert outcome.fallback is True  # 复用模板回退徽章通道
    assert outcome.fallback_reason == "coverage"
    assert outcome.generated is False
    assert "不锈钢" in outcome.agent_message.content  # 模板=证据原文
    assert outcome.answer.kind == "answer"


def test_high_coverage_single_hit_still_generates(
    engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 覆盖满格的单命中照旧走生成（闸只收薄证据）
    hits = [_hit(3, "叉子：不锈钢")]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {3: {"kind": "document", "title": "叉子说明"}},
    )

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "叉子是什么材质的？"))

    assert engine["calls"]["stream_called"] == 1
    assert outcome.fallback is False
    assert outcome.fallback_reason is None
    assert outcome.generated is True
    assert outcome.agent_message.content == "模型回答"


def test_multi_hit_bypasses_gate(engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    # 命中>1 不受闸影响（ADR 0044 §二：命中数≤1 才降级）
    hits = [_hit(3, "叉子：不锈钢"), _hit(9, "无关的其它证据句")]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {3: {"kind": "document", "title": "叉子说明"}},
    )

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "这个叉子能不能进洗碗机消毒"))

    assert engine["calls"]["stream_called"] == 1
    assert outcome.fallback is False
    assert outcome.fallback_reason is None


def test_refusal_path_untouched_by_gate(
    engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 零命中照旧拒答（0018 不动）：不调模型、非 fallback、缺口语义不变
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: [])
    monkeypatch.setattr("suite_api.services.chat_engine.record_refusal_gap", lambda *_a, **_k: None)

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "会员生日礼怎么领？"))

    assert engine["calls"]["stream_called"] == 0
    assert outcome.answer.kind == "refusal"
    assert outcome.fallback is False
    assert outcome.fallback_reason is None


# ---------- 第 58 刀：弱命中但模型自述「证据未覆盖」-> 按拒答收口 ----------


def test_model_declares_no_coverage_becomes_refusal(
    engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """审计刀 11 C-P1-1：有弱命中时模型答「证据未覆盖…」也是一种「答不了」。

    此前它被记成 kind=answer（还挂引用）、既不落缺口也不转人工——同一处知识缺失
    因为「检索有没有边际命中」产生两种系统状态。第 58 刀起按拒答收口。
    """

    def fake_no_coverage(_system: str, _user: str, history: list[dict[str, str]] | None = None):
        del history

        async def _gen() -> Any:
            yield "当前已发布证据未覆盖刻字收费信息。"

        return _gen()

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", fake_no_coverage)
    # 缺口记录用替身：真函数 flush 后才有 id，MagicMock 会话给不出 id（拒答文案要格式化）
    def _fake_gap(*_a: Any, **_k: Any) -> Any:
        engine["calls"]["gap_called"] += 1
        return type("G", (), {"id": 1})()

    monkeypatch.setattr("suite_api.services.chat_engine.record_refusal_gap", _fake_gap)
    # 两条命中（覆盖够，忠实度闸不触发）——正是审计刀 11 实测的那条路径
    hits = [_hit(3, "叉子：不锈钢"), _hit(9, "客服：整机一年保修。")]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {3: {"kind": "document", "title": "叉子说明"}, 9: {"kind": "dialogue", "title": "保修"}},
    )

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "保温杯刻字怎么收费？"))

    assert outcome.answer.kind == "refusal"  # 不是 answer
    assert outcome.answer.citations == []  # 引用清零（不再拿引用背「没答」）
    assert outcome.answer.handoff is True  # 转人工
    assert outcome.fallback_reason == "no_coverage"  # 可观测的触发原因
    assert outcome.generated is False
    # 缺口照落（同一知识洞只有一种形态）：断言**真调了** record_refusal_gap 且拿到行
    # （审计刀 12：原来用 `db.add.called` 恒真——run_ask 开头就 add 顾客消息）
    assert engine["calls"]["gap_called"] == 1
    assert outcome.gap is not None


def test_real_answer_is_not_mistaken_for_no_coverage(
    engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向钉子：正常作答（含「证据」字样但不含未覆盖谓语）不许被误判成拒答。"""

    def fake_answer(_system: str, _user: str, history: list[dict[str, str]] | None = None):
        del history

        async def _gen() -> Any:
            yield "根据已发布证据，净含量为 500ml。[1]"

        return _gen()

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", fake_answer)
    hits = [_hit(3, "净含量：500ml"), _hit(9, "客服：净含量为500ml。")]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {3: {"kind": "document", "title": "规格"}, 9: {"kind": "dialogue", "title": "对话"}},
    )

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "保温杯的净含量是多少？"))

    assert outcome.answer.kind == "answer"
    assert outcome.fallback_reason is None


@pytest.mark.parametrize(
    "answer_text",
    [
        "配料信息中不包含任何防腐剂。",  # 「信息」是商品信息，不是证据
        "产品信息不包含电池，需另行购买。",
        "我无法提供比这更详细的信息。",  # 动词与宾语之间不该跨填充词
        "客服无法确认该订单信息，请提供订单号。",
        "根据已发布证据，净含量为 500ml。[1]",
    ],
)
def test_no_coverage_gate_does_not_fire_on_normal_answers(
    engine: dict[str, Any], monkeypatch: pytest.MonkeyPatch, answer_text: str
) -> None:
    """审计刀 12 P0：闸的误判面比漏检更重（会把答对的整条收成拒答 + 落缺口 + 建工单）。

    这五条都是正常作答/正常措辞，不许被当成「覆盖声明」。
    """

    def fake(_system: str, _user: str, history: list[dict[str, str]] | None = None):
        del history

        async def _gen() -> Any:
            yield answer_text

        return _gen()

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", fake)
    hits = [_hit(3, "配料：不含防腐剂"), _hit(9, "客服：材质说明。")]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {3: {"kind": "document", "title": "配料"}, 9: {"kind": "dialogue", "title": "对话"}},
    )

    outcome = asyncio.run(run_ask(engine["db"], engine["session"], "配料里有什么？"))

    assert outcome.answer.kind == "answer"
    assert outcome.fallback_reason is None
    assert engine["calls"]["gap_called"] == 0  # 不许落缺口


# ---------- 审计刀 12 P0：部分可答不许被整条吞掉 ----------


@pytest.mark.parametrize(
    ("answer_text", "expect"),
    [
        # 部分答对 + 免责句 -> 保留答对那部分（摘掉免责句）
        ("签收后7天内可申请退货。[1][2] 具体退货操作证据未覆盖。", "签收后7天内可申请退货。"),
        ("已发布证据未提及运费。[1] 材质为钛钢。[2]", "材质为钛钢。[2]"),
        # 全是免责句 -> 按拒答收口
        ("现有证据未覆盖刻字收费信息。", ""),
        ("证据未提供价格。", ""),
        # 单句免责 + 引用标记：摘完只剩 [1][2] 也算「没答」（不许把光秃秃的引用标记答出去）
        ("已发布证据未说明保温杯可保温几个小时。[1][2]", ""),
        # 正常作答原样保留
        ("根据已发布证据，净含量为 500ml。[1]", "根据已发布证据，净含量为 500ml。[1]"),
    ],
)
def test_strip_coverage_disclaimers(answer_text: str, expect: str) -> None:
    """审计刀 12 P0：按句判——只有整段都是覆盖声明才拒答。

    实测（内置建议问句「怎么退货？」）：模型输出「签收后7天内可申请退货。[1][2]
    具体退货操作证据未覆盖。」时，整段命中会把**已被证据支撑的那句**也丢掉，问句
    随机变拒答（2/3 概率）+ 落缺口 + 建工单。
    """
    from suite_api.services.chat_engine import strip_coverage_disclaimers

    remaining, all_disclaimers = strip_coverage_disclaimers(answer_text)
    if expect == "":
        assert all_disclaimers is True
        assert remaining == ""
    else:
        assert all_disclaimers is False
        assert expect in remaining
        assert "未覆盖" not in remaining and "未提供" not in remaining
