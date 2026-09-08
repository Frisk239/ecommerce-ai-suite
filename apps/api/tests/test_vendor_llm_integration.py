"""厂商生成集成测试（真 PG，LLM 全 mock 不调外网；见 conftest 的空凭证清理）。

第 7 刀契约（ADR 0033 + spec）：
- 有证据 -> llm.stream_chat 被调且 prompt 含证据与来源标注；SSE=thinking(检索)
  -> thinking(生成) -> delta* -> complete，delta 拼接=模型全文、citations=检索
  命中（服务端定，0007）、fallback=false。
- LLM 失败/空产出 -> 证据组装模板回答走同一 delta 流 + fallback=true（模板
  逻辑复用 answer.py，不改）。
- 无证据 -> 拒答且 stream_chat 不被调（0018：防编造省调用）。
- 空 LLM_API_KEY（conftest 强制 env）-> 同样降级模板（既有测试缺省路径）。
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sse_helpers import parse_sse_events

from suite_api.models import ServiceMessage
from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]

# 本文件独有关键词（module 独立库，不与其他文件已发布块串台）。三用例字段词
# 互不共享 bigram（否则先发布的块串台命中后用例的问句）；降级用例的文档首行
# 也刻意避开问句 bigram（否则标题行块命中，模板回答成两句）。
_VENDOR_DOC = "厂商生成验证说明\n试饮装容量：520ml".encode()
_FALLBACK_DOC = "降级口径备注\n回退批号：460ml".encode()
_UNKEYED_DOC = "密钥缺席口径备注\n空箱数量：410ml".encode()
_MODEL_PIECES = ["这款保温杯的试饮装容量", "为520ml，", "双层真空结构，日常使用足够。"]
_MODEL_FULL_ANSWER = "".join(_MODEL_PIECES)
_FALLBACK_TEMPLATE = "根据已发布的规格文档《降级口径备注》，回退批号为460ml。"
_UNKEYED_TEMPLATE = "根据已发布的规格文档《密钥缺席口径备注》，空箱数量为410ml。"


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"username": "operator", "password": "operator123"}).status_code == 200


def _upload_and_publish(client: TestClient, content: bytes, title: str) -> int:
    resp = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", content, "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _ask(client: TestClient, question: str) -> tuple[int, list[tuple[str, dict]]]:
    """自建会话并发问；返回 (session_id, events)——session_id 供落库断言。"""
    session_id = client.post("/api/service/sessions").json()["id"]
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return session_id, parse_sse_events(raw)


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch,
    pieces: list[str] | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    """替换 llm.stream_chat 为 fake 流（async generator）；返回 prompt 捕获记录。"""
    calls: list[dict[str, str]] = []

    async def fake_stream(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        for piece in pieces or []:
            yield piece

    monkeypatch.setattr(llm_module, "stream_chat", fake_stream)
    return calls


def _agent_content(client: TestClient, session_id: int) -> str:
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        agent = db.scalar(
            select(ServiceMessage)
            .where(ServiceMessage.session_id == session_id, ServiceMessage.role == "agent")
            .order_by(ServiceMessage.id.desc())
        )
        assert agent is not None
        return agent.content


# ---------- 模型路径：真 token 流 + 服务端定 citations ----------


def test_vendor_stream_answer_citations_and_prompts(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    asset_id = _upload_and_publish(client, _VENDOR_DOC, "厂商生成验证说明")
    calls = _patch_stream(monkeypatch, pieces=_MODEL_PIECES)

    session_id, events = _ask(client, "试饮装容量是多少？")

    # prompt 契约：被调一次；证据块带来源标注与字段值；不含任何密钥形态
    assert len(calls) == 1
    assert f"[来源：A-{asset_id}·v1] 试饮装容量：520ml" in calls[0]["user"]
    assert "顾客问题：试饮装容量是多少？" in calls[0]["user"]
    assert "只依据" in calls[0]["system"]
    assert "sk-" not in calls[0]["user"] + calls[0]["system"]

    # SSE 序列：双 thinking（按到达顺序渲染状态行）-> delta* -> complete
    kinds = [event for event, _ in events]
    assert kinds[0] == "thinking" and events[0][1]["text"] == "正在检索已发布资产…"
    assert kinds[1] == "thinking" and events[1][1]["text"] == "正在生成回答…"
    assert kinds[-1] == "complete"
    deltas = [data["text"] for event, data in events if event == "delta"]
    assert "".join(deltas) == _MODEL_FULL_ANSWER  # delta 拼接=完整模型文本
    assert all(len(piece) <= 12 for piece in deltas)  # 切片粒度与既有节流一致

    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["handoff"] is False
    assert complete["fallback"] is False  # 模型成功：无回退
    assert complete["citations"] == [{"asset_id": asset_id, "version_no": 1}]  # 服务端定
    assert complete["gap_id"] is None

    # 落库：完整生成文本进 agent 消息（断连=完整落库契约同源）
    assert _agent_content(client, session_id) == _MODEL_FULL_ANSWER


# ---------- 降级路径 ----------


def test_vendor_failure_falls_back_to_template(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 失败 -> answer.py 模板回答 + fallback=true；无「正在生成」thinking。"""
    client, _ = api
    _login(client)
    asset_id = _upload_and_publish(client, _FALLBACK_DOC, "降级口径备注")
    calls = _patch_stream(monkeypatch, error=llm_module.LLMUnavailable("厂商模型暂时不可用"))

    _, events = _ask(client, "回退批号是多少？")
    thinking_texts = [data["text"] for event, data in events if event == "thinking"]
    assert thinking_texts == ["正在检索已发布资产…"]  # 降级不发「正在生成回答…」
    deltas = "".join(data["text"] for event, data in events if event == "delta")
    assert deltas == _FALLBACK_TEMPLATE  # 复用 answer.py 模板（只复用不改）
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["fallback"] is True
    assert complete["citations"] == [{"asset_id": asset_id, "version_no": 1}]
    assert len(calls) == 1  # 确实尝试过模型（失败在厂商侧，不是没调）

    # 空产出（模型只吐空白）同样降级：不把空回答当成功落库
    _patch_stream(monkeypatch, pieces=["  ", ""])
    _, empty_events = _ask(client, "回退批号是多少？")
    assert empty_events[-1][1]["fallback"] is True
    empty_deltas = "".join(data["text"] for event, data in empty_events if event == "delta")
    assert empty_deltas == _FALLBACK_TEMPLATE


def test_empty_api_key_falls_back_to_template(api: ApiFixture) -> None:
    """不 mock：conftest 强制空 LLM_API_KEY -> stream_chat 自身抛 LLMNotConfigured
    -> 降级模板。这同时是既有测试环境的缺省路径（无凭证 env 自动走模板）。"""
    client, _ = api
    _login(client)
    asset_id = _upload_and_publish(client, _UNKEYED_DOC, "密钥缺席口径备注")
    _, events = _ask(client, "空箱数量是多少？")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["fallback"] is True
    assert complete["citations"] == [{"asset_id": asset_id, "version_no": 1}]
    deltas = "".join(data["text"] for event, data in events if event == "delta")
    assert deltas == _UNKEYED_TEMPLATE


# ---------- 无证据：拒答且不调模型（0018） ----------


def test_no_evidence_never_calls_llm(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    calls = _patch_stream(
        monkeypatch,
        error=AssertionError("无证据问题不得调用厂商模型（0018：防编造省调用）"),
    )

    _, events = _ask(client, "冥王星殖民基地怎么预约参观？")
    complete = events[-1][1]
    assert complete["kind"] == "refusal"
    assert complete["handoff"] is True
    assert complete["citations"] == []
    assert complete["fallback"] is False  # 拒答不是降级
    assert isinstance(complete["gap_id"], int)  # 拒答照常落知识缺口（0024）
    # 第 27 刀拒答交接摘要：原「全等固定文案」断言按新语义更新——无证据不调
    # 模型不变，落库/流式文本带问句摘要与缺口段（操作者通道）
    refusal_deltas = "".join(data["text"] for event, data in events if event == "delta")
    assert refusal_deltas == (
        "抱歉，已发布资产里没有能回答这个问题的证据。\n"
        "问句摘要：冥王星殖民基地怎么预约参观？\n"
        f"缺口：G-{complete['gap_id']:04d}"
    )
    assert calls == []  # stream_chat 从未被调（mock 断言 call count=0）
