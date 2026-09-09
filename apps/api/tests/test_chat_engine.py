"""客服引擎单测：LLM 等待前结束只读事务（不占连接）。"""

import asyncio
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

    assert order == ["commit", "commit", "stream_chat", "commit"]
