"""第 108B 刀（W2）：回答路径标识（complete.path）——tool/template/llm/refusal。

后端纯标注：complete 载荷加 ``path``（``answer_path`` 纯函数从既有 outcome 字段
派生），前端据此在气泡角落出「工具/模板/AI/拒答」标签——让「什么时候没调大模型」
一眼可见。**零分支行为改动**：同一条回答的 kind/citations/fallback/事件序逐字
不变（本文件同时钉住这点）。

四态口径：refusal=拒答（无证据/无覆盖/OOV）；llm=模型真产出正文；tool=确定性
工具/查询（订单/库存/退货资格/目录/澄清/元回声，answer 与 handoff 两 kind 都算）；
template=证据组装模板回退与纯文案出口（显式转人工回执/提议被拒）。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services import llm as llm_module
from suite_api.services.chat_engine import AskOutcome, answer_path, run_ask, sse_event_stream

ApiFixture = tuple[TestClient, Path]

_DOC = "路径标识验证说明\n试饮批号：530ml".encode()
_MODEL_PIECES = ["试饮批号是530ml。"]
_UNANSWERABLE = "登山绳可以定制长度吗"  # 本文件独有关键词：零命中（拒答路径）


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _upload_and_publish(client: TestClient, content: bytes, title: str) -> int:
    resp = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", content, "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201
    asset_id = int(resp.json()["id"])
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _ask(client: TestClient, question: str) -> list[tuple[str, dict]]:
    session_id = client.post("/api/service/sessions").json()["id"]
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _outcome(**overrides: Any) -> AskOutcome:
    """最小替身 outcome（answer_path 只读 answer.kind/generated/tool）。"""
    defaults: dict[str, Any] = {
        "agent_message": SimpleNamespace(id=1),
        "answer": SimpleNamespace(kind="answer", handoff=False, citations=[]),
        "generated": False,
        "fallback": False,
    }
    defaults.update(overrides)
    return AskOutcome(gap=None, **defaults)


# ---------- 纯函数：四态派生（不碰 DB） ----------


def test_answer_path_pure_derivation() -> None:
    # 无工具无生成（模板/回执面）：template
    assert answer_path(_outcome()) == "template"
    assert (
        answer_path(
            _outcome(
                answer=SimpleNamespace(kind="refusal", handoff=True, citations=[]),
                tool={"name": "get_stock", "arg": "杯", "result": "无"},
            )
        )
        == "refusal"
    )
    # 模型产出正文：即使提议步工具也跑过（混意图轮）仍归 llm（终稿是模型写的）
    assert answer_path(_outcome(generated=True)) == "llm"
    assert answer_path(_outcome(generated=True, tool={"name": "get_stock", "arg": "a"})) == "llm"
    # 工具/查询路径（answer 与 handoff 两 kind 都算）
    assert answer_path(_outcome(tool={"name": "get_order_status", "arg": "SO-1"})) == "tool"
    assert (
        answer_path(
            _outcome(
                answer=SimpleNamespace(kind="handoff", handoff=True, citations=[]),
                tool={"name": "get_stock", "arg": "a", "result": "未设置"},
            )
        )
        == "tool"
    )
    # 纯文案出口：显式转人工回执 / 提议被拒（rejected 工具条不是工具查询）
    assert answer_path(_outcome(tool={"name": "handoff", "arg": "词表"})) == "template"
    assert (
        answer_path(
            _outcome(tool={"name": "get_order_status", "arg": "x", "rejected": True})
        )
        == "template"
    )


def test_complete_carries_path_on_both_channels() -> None:
    """两通道同形状（白名单只管内部 id：gap_id/fallback_reason）。"""
    message = SimpleNamespace(id=7, content="答", citations=[])
    outcome = _outcome(
        agent_message=message, tool={"name": "get_order_status", "arg": "SO-1001"}
    )
    operator = parse_sse_events("".join(sse_event_stream(outcome, expose_gap_id=True)))
    customer = parse_sse_events("".join(sse_event_stream(outcome, expose_gap_id=False)))
    assert operator[-1][1]["path"] == "tool"
    assert customer[-1][1]["path"] == "tool"


# ---------- 端到端：四条路径各一条（真引擎 SSE） ----------


def test_tool_path_end_to_end(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    events = _ask(client, "我的订单 SO-1001 到哪了")
    complete = events[-1][1]
    assert complete["path"] == "tool"
    assert complete["tool"]["name"] == "get_order_status"
    # 纯标注：既有键逐字不变
    assert complete["kind"] == "answer" and complete["handoff"] is False
    assert events[0] == ("thinking", {"text": "查询订单中…"})


def test_template_path_end_to_end(api: ApiFixture) -> None:
    """空 LLM 凭证（conftest 强制）-> 证据组装模板回退，path=template。"""
    client, _ = api
    _login(client)
    _upload_and_publish(client, _DOC, "路径标识验证说明")
    events = _ask(client, "试饮批号是多少？")
    complete = events[-1][1]
    assert complete["path"] == "template"
    assert complete["fallback"] is True  # 既有徽章口径不变
    assert complete["citations"]  # 证据引用照旧（模板也带引用）


def test_llm_path_end_to_end(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    _upload_and_publish(client, _DOC, "路径标识验证说明")

    async def fake_stream(
        _system: str, _user: str, history: list[dict[str, str]] | None = None
    ) -> Any:
        for piece in _MODEL_PIECES:
            yield piece

    monkeypatch.setattr(llm_module, "stream_chat", fake_stream)
    events = _ask(client, "试饮批号是多少？")
    complete = events[-1][1]
    assert complete["path"] == "llm"
    assert complete["fallback"] is False
    assert [event for event, _ in events][:2] == ["thinking", "thinking"]  # 生成状态行照旧


def test_refusal_path_end_to_end(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    events = _ask(client, _UNANSWERABLE)
    complete = events[-1][1]
    assert complete["path"] == "refusal"
    assert complete["kind"] == "refusal" and complete["handoff"] is True
    assert complete["citations"] == []


def test_handoff_receipt_path_is_template(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    events = _ask(client, "我要转人工")
    complete = events[-1][1]
    assert complete["path"] == "template"  # 回执是固定文案，不是工具查询也不是模型生成
    assert complete["kind"] == "handoff" and complete["ticket_no"].startswith("H-")


def test_answer_path_matches_outcome_across_engine_calls(api: ApiFixture) -> None:
    """引擎直调复核：complete.path 与实际 outcome（工具/gap/生成）自洽。"""
    client, _ = api
    _login(client)
    factory = client.app.state.session_factory
    from suite_api.models import ServiceSession

    with factory() as db:
        session = ServiceSession(status="active")
        db.add(session)
        db.commit()
        meta = asyncio.run(run_ask(db, session, "我刚才问了什么"))
        assert meta.tool is not None and meta.tool["name"] == "meta_echo"
        assert answer_path(meta) == "tool"  # 确定性路径（查了会话历史，非模板非模型）
