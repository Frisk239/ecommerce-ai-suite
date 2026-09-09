"""多轮记忆集成测试（真 PG，LLM monkeypatch 不调外网；第 29 刀 feat/multi-turn）。

契约（spec Must 4，裁决见 .scratch/multi-turn/spec.md）：
- 问 B（有证据）-> 问 C（含代词）：C 的生成调用携带 B 轮历史（messages 化
  history），且检索词补全生效——C 单问无证据（对照会话拒答）、拼接上一问后
  命中 B 主题资产（行为验证：citations=B 资产、kind=answer 而非拒答）。
- 拒答轮不进记忆：拒答后的下一答 history 为空，拒答文案与问句都不进 prompt。
- 工具轮不进记忆：订单工具轮（agent.tool 非空）整轮跳过。
- 顾客通道同享（0021 同一引擎）：patch 同断言。
- 记忆不制造证据：全新会话首问含代词 -> 无历史不拼接 -> 无证据仍拒答，
  stream_chat 不被调（0018 防编造省调用）。
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services import llm as llm_module

ApiFixture = tuple[TestClient, Path]

# 本文件独有关键词（module 独立库）：「钛杯/净含量」只被 B 问命中；C 问的
# 「保修/政策」在任何块里都没有——C 单问必无证据，拼接 B 问后才命中。
_MULTI_DOC = "多轮记忆钛杯验证说明\n净含量：500ml".encode()
_Q_TOPIC = "钛杯的净含量是多少"  # B 问：命中标题块+字段块
_Q_PRONOUN = "那它的保修政策是什么"  # C 问：含代词「它」，单问无证据
_Q_REFUSAL = "会员生日礼盒怎么领"  # 无证据拒答问句
_Q_ORDER = "订单 SO-9999 怎么还没到"  # 订单工具轮（seed 无此单 -> handoff）
_MODEL_PIECES = ["净含量为500ml，", "杯身双层真空。"]
_MODEL_ANSWER = "".join(_MODEL_PIECES)


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
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _customer_ask(
    client: TestClient, session_id: int, token: str, question: str
) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": question},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch,
    pieces: list[str] | None = None,
    error: Exception | None = None,
) -> list[dict[str, Any]]:
    """替换 llm.stream_chat 为 fake 流；返回捕获记录（含 history 实参）。"""
    calls: list[dict[str, Any]] = []

    async def fake_stream(
        system_prompt: str, user_prompt: str, history: list[dict[str, str]] | None = None
    ) -> Any:
        calls.append(
            {"system": system_prompt, "user": user_prompt, "history": list(history or [])}
        )
        if error is not None:
            raise error
        for piece in pieces or []:
            yield piece

    monkeypatch.setattr(llm_module, "stream_chat", fake_stream)
    return calls


# module 独立库内只上传发布一次、各用例共用同一资产：同文多副本会让检索
# 并列分数按 (asset_id, chunk) 稳定排序，后传的副本进不了 citations 前二、
# 搅动断言——单资产下 citations 恒 [{asset_id, v1}]，确定性钉死
_ASSET_CACHE: dict[str, int] = {}


def _ensure_published(client: TestClient) -> int:
    if "asset_id" not in _ASSET_CACHE:
        _ASSET_CACHE["asset_id"] = _upload_and_publish(client, _MULTI_DOC, "多轮记忆钛杯验证说明")
    return _ASSET_CACHE["asset_id"]


def _prompt_blob(call: dict[str, Any]) -> str:
    """一次生成调用的全部 prompt 文本（system+user+history 消息内容）。"""
    return call["system"] + call["user"] + "".join(m["content"] for m in call["history"])


# ---------- 主路径：代词问句携带历史 + 检索词补全（行为验证） ----------


def test_pronoun_question_carries_history_and_expanded_retrieval(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    asset_id = _ensure_published(client)
    calls = _patch_stream(monkeypatch, pieces=_MODEL_PIECES)
    sid = client.post("/api/service/sessions").json()["id"]

    # 问 B：有证据，首轮无历史
    events_b = _ask(client, sid, _Q_TOPIC)
    assert events_b[-1][1]["kind"] == "answer"
    assert len(calls) == 1 and calls[0]["history"] == []

    # 问 C（含代词「它」）：B 轮进历史 messages；检索词补全=拼接上一问——
    # C 单问无证据，拼接后命中 B 主题资产（refusal 变 answer 即行为证明）
    events_c = _ask(client, sid, _Q_PRONOUN)
    complete_c = events_c[-1][1]
    assert complete_c["kind"] == "answer"
    assert complete_c["citations"] == [{"asset_id": asset_id, "version_no": 1}]
    assert len(calls) == 2
    assert calls[1]["history"] == [
        {"role": "customer", "content": _Q_TOPIC},
        {"role": "agent", "content": _MODEL_ANSWER},
    ]
    # 本轮问句仍是 user 段的「顾客问题」行（证据+问句形状不动，0007/0033）
    assert f"顾客问题：{_Q_PRONOUN}" in calls[1]["user"]
    assert _Q_TOPIC not in calls[1]["user"]  # 历史不混进 user 段，走独立 messages

    # 对照：全新会话同句首问——无历史不拼接，无证据仍拒答（记忆不制造证据）
    fresh_sid = client.post("/api/service/sessions").json()["id"]
    fresh_events = _ask(client, fresh_sid, _Q_PRONOUN)
    assert fresh_events[-1][1]["kind"] == "refusal"
    assert len(calls) == 2  # 拒答不调模型


# ---------- 跳过规则：拒答轮与工具轮不进下一问的 prompt ----------


def test_refusal_turn_excluded_from_next_prompt(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _ensure_published(client)
    calls = _patch_stream(monkeypatch, pieces=_MODEL_PIECES)
    sid = client.post("/api/service/sessions").json()["id"]

    refusal_events = _ask(client, sid, _Q_REFUSAL)
    assert refusal_events[-1][1]["kind"] == "refusal"

    answer_events = _ask(client, sid, _Q_TOPIC)
    assert answer_events[-1][1]["kind"] == "answer"
    assert len(calls) == 1
    # 拒答轮整轮跳过：history 空，拒答文案与拒答问句都不进任何 prompt 段
    assert calls[0]["history"] == []
    blob = _prompt_blob(calls[0])
    assert "抱歉" not in blob
    assert _Q_REFUSAL not in blob


def test_tool_turn_excluded_from_memory(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    _ensure_published(client)
    calls = _patch_stream(monkeypatch, pieces=_MODEL_PIECES)
    sid = client.post("/api/service/sessions").json()["id"]

    # 订单工具轮（seed 无 SO-9999 -> handoff，agent.tool 非空）
    tool_events = _ask(client, sid, _Q_ORDER)
    tool_kinds = [event for event, _ in tool_events]
    assert "tool" in tool_kinds
    assert tool_events[-1][1]["kind"] == "handoff"

    answer_events = _ask(client, sid, _Q_TOPIC)
    assert answer_events[-1][1]["kind"] == "answer"
    assert len(calls) == 1
    # 工具轮整轮跳过（含其问句）：订单号与工具回答都不进历史/prompt
    assert calls[0]["history"] == []
    blob = _prompt_blob(calls[0])
    assert "SO-9999" not in blob
    assert "订单" not in blob


# ---------- 顾客通道同享（0021 同一引擎） ----------


def test_customer_channel_shares_multi_turn_memory(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)  # 操作者备证据
    asset_id = _ensure_published(client)
    client.cookies.clear()  # 之后全走顾客通道
    calls = _patch_stream(monkeypatch, pieces=_MODEL_PIECES)
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]

    events_b = _customer_ask(client, sid, token, _Q_TOPIC)
    assert events_b[-1][1]["kind"] == "answer"

    events_c = _customer_ask(client, sid, token, _Q_PRONOUN)
    complete_c = events_c[-1][1]
    assert complete_c["kind"] == "answer"
    assert complete_c["citations"] == [{"asset_id": asset_id, "version_no": 1}]
    assert "gap_id" not in complete_c  # 载荷白名单与多轮记忆互不影响
    assert len(calls) == 2
    assert calls[1]["history"] == [
        {"role": "customer", "content": _Q_TOPIC},
        {"role": "agent", "content": _MODEL_ANSWER},
    ]


# ---------- 记忆不制造证据：无历史含代词问仍拒答且不调模型 ----------


def test_pronoun_first_question_without_memory_never_calls_llm(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _ensure_published(client)
    calls = _patch_stream(
        monkeypatch,
        error=AssertionError("无证据问题不得调用厂商模型（0018：防编造省调用）"),
    )
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, _Q_PRONOUN)
    complete = events[-1][1]
    assert complete["kind"] == "refusal"
    assert complete["handoff"] is True
    assert complete["citations"] == []
    assert isinstance(complete["gap_id"], int)  # 拒答照常落缺口（0024）
    assert calls == []  # stream_chat 从未被调
