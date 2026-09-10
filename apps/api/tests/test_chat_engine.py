"""客服引擎单测：LLM 等待前结束只读事务（不占连接）。"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from suite_api.services.answer import ComposedAnswer
from suite_api.services.chat_engine import run_ask


def test_run_ask_commits_before_llm_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """retrieve + compose 之后、await stream_chat 之前必须 commit。"""
    order: list[str] = []
    db = MagicMock()
    db.commit.side_effect = lambda: order.append("commit")
    session = MagicMock()
    session.id = 1

    hits = [{"asset_id": 1, "version_no": 1, "chunk": "净含量：480ml", "score": 1.0}]
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: hits)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.assets_meta",
        lambda *_a, **_k: {1: {"kind": "document", "title": "规格"}},
    )
    monkeypatch.setattr(
        "suite_api.services.chat_engine.compose_answer",
        lambda *_a, **_k: ComposedAnswer(
            content="模板",
            citations=[{"asset_id": 1, "version": 1}],
            kind="answer",
            handoff=False,
        ),
    )
    monkeypatch.setattr(
        "suite_api.services.chat_engine.llm.build_prompts",
        lambda *_a, **_k: ("sys", "usr"),
    )

    async def fake_stream(
        _system: str, _user: str, history: list[dict[str, str]] | None = None
    ) -> Any:
        # 第 29 刀多轮化：stream_chat 增 history 参数（默认 None）——替身按新
        # 形状补默认参数，行为断言（commit 序）零改动
        order.append("stream_chat")
        yield "模型回答"

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", fake_stream)

    asyncio.run(run_ask(db, session, "净含量"))

    # 第 37 刀（ADR 0043）适配：新循环有两个 LLM 等待点，各自遵守「等待前
    # 结束只读事务」纪律——①customer 问句 commit ②提议步 recent_turns 读后、
    # complete_chat 前 commit（stream_chat 被本测试替身替换后 complete_chat
    # 内部复用同一通道，替身串里记作 stream_chat）③检索+组装后、生成
    # stream_chat 前 commit ④agent 消息落库 commit。纪律不变，形状+1 步。
    # 第 37 刀（ADR 0043）适配：新循环有两个 LLM 等待点，各自遵守「等待前
    # 结束只读事务」纪律——①customer 问句 commit ②提议步 recent_turns 读后、
    # complete_tool_proposal（专用非流式通道，不复用 stream_chat 哨兵位）
    # 前 commit；空 key 下提议步 LLMNotConfigured 降级走检索（替身未触及）
    # ③检索+组装后、生成 stream_chat 前 commit ④agent 消息落库 commit。
    # 纪律不变，形状多一次「提议前 commit」。
    assert order == [
        "commit",  # customer 问句落库
        "commit",  # 提议步 LLM 等待前归还连接（空 key 降级，调用未发生）
        "commit",  # 检索+组装后、生成等待前归还连接
        "stream_chat",  # 生成步 LLM 调用（替身）
        "commit",  # agent 消息落库
    ]


def test_customer_channel_hides_internal_fallback_reason() -> None:
    """审计刀 12 P2：`fallback_reason` 是内部闸口径，只给操作者通道。

    （顾客面只需要 `fallback` 布尔——徽章用；与 gap_id 同一白名单思路。）
    """
    from suite_api.services.chat_engine import AskOutcome, sse_event_stream

    # SSE 生成器只读 agent_message.content / answer.kind / answer.citations——
    # 用最小替身（SimpleNamespace；MagicMock 不能进 json.dumps）
    outcome = AskOutcome(
        agent_message=SimpleNamespace(
            content="抱歉，已发布资产里没有能回答这个问题的证据。",
            citations=[],
            kind="refusal",
            handoff=True,
            ticket=None,
            id=1,
        ),
        answer=SimpleNamespace(kind="refusal", handoff=True, citations=[]),
        gap=None,
        generated=False,
        fallback=False,
        fallback_reason="no_coverage",
    )

    operator_events = "".join(sse_event_stream(outcome, expose_gap_id=True))
    customer_events = "".join(sse_event_stream(outcome, expose_gap_id=False))
    assert "no_coverage" in operator_events
    assert "no_coverage" not in customer_events
    assert '"fallback": false' in customer_events  # 布尔仍在（前端徽章）
