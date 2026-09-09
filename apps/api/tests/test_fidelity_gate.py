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
    calls: dict[str, Any] = {"stream_called": 0, "prompt": None}
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
