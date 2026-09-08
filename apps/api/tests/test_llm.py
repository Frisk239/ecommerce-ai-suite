"""厂商 LLM 客户端单测（无 DB、无外网：openai 客户端以替身注入/替换）。

密钥纪律（0033）在这里钉死：空 key 不建客户端；下游异常消息不含密钥/端点；
prompt 组装不触碰凭证。
"""

import asyncio
from types import SimpleNamespace as NS
from typing import Any

import pytest

from suite_api.services import llm
from suite_api.settings import Settings


def _hit(asset_id: int, version_no: int, chunk: str, score: float = 1.0) -> dict:
    return {"asset_id": asset_id, "version_no": version_no, "chunk": chunk, "score": score}


# ---------- prompt 组装 ----------


def test_build_prompts_marks_sources_and_includes_field_values() -> None:
    """证据块各带「来源：A-{id}·v{N}」（0007 版本口径）；确认字段值随命中
    切块进 prompt（发布事务入索引的「字段：值」块，无第二条取数路径）。"""
    hits = [
        _hit(3, 1, "净含量：480ml", score=1.4),
        _hit(9, 2, "客服：净含量为480ml。", score=1.1),
    ]
    system_prompt, user_prompt = llm.build_prompts(hits, "保温杯的净含量是多少？")
    assert "[来源：A-3·v1] 净含量：480ml" in user_prompt
    assert "[来源：A-9·v2] 客服：净含量为480ml。" in user_prompt
    assert "顾客问题：保温杯的净含量是多少？" in user_prompt
    # 系统提示要点（任务锁定）：只依据证据、不编造、简洁、不输出引用编号
    for keyword in ("只依据", "证据", "不要编造", "简洁", "不要输出引用编号"):
        assert keyword in system_prompt


def test_build_prompts_caps_evidence_and_never_touches_credentials() -> None:
    """证据条数收口（宁短而准）；prompt 组装不碰凭证——输出里不可能出现
    密钥形态（钉死契约，防未来改动把 settings 值拼进 prompt）。"""
    hits = [_hit(i, 1, f"证据句{i}") for i in range(1, 7)]
    system_prompt, user_prompt = llm.build_prompts(hits, "问题")
    assert user_prompt.count("[来源：") == llm._MAX_EVIDENCE
    assert "证据句4" not in user_prompt  # 超出上限的命中不进 prompt
    secret = "sk-test-1234567890"
    assert secret not in system_prompt
    assert secret not in user_prompt
    assert "api_key" not in user_prompt


# ---------- stream_chat：配置门槛与异常契约 ----------


def test_llm_error_hierarchy_lets_caller_catch_once() -> None:
    """降级判定的契约钉子：未配置/不可用都是 LLMError 子类，调用方一个
    except llm.LLMError 全兜走降级路径。"""
    assert issubclass(llm.LLMNotConfigured, llm.LLMError)
    assert issubclass(llm.LLMUnavailable, llm.LLMError)


def _consume(system_prompt: str = "s", user_prompt: str = "u") -> list[str]:
    async def run() -> list[str]:
        return [piece async for piece in llm.stream_chat(system_prompt, user_prompt)]

    return asyncio.run(run())


def test_stream_chat_without_key_raises_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 LLM_API_KEY -> LLMNotConfigured（不建客户端、不发请求；CI/无 .env
    环境直接走调用方降级，不炸外网）。"""
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: Settings(llm_api_key="", llm_base_url="http://localhost:9/v1", llm_model="m"),
    )
    monkeypatch.setattr(llm, "_client", None)
    with pytest.raises(llm.LLMNotConfigured):
        _consume()


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, create_result: Any) -> list[dict]:
    """注入替身 openai 客户端（绕过 settings/网络），返回 create 调用参数记录。"""
    calls: list[dict] = []

    class Completions:
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return create_result

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    monkeypatch.setattr(llm, "_client", Client())
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: Settings(
            llm_api_key="sk-test-1234567890", llm_base_url="http://localhost:9/v1", llm_model="fake-model"
        ),
    )
    return calls


class _FakeStream:
    """openai 流块形状：chunk.choices[0].delta.content（含 None 增量首块）。"""

    def __init__(self, chunks: list[str | None], fail_on: int | None = None) -> None:
        self._chunks = chunks
        self._fail_on = fail_on
        self._i = 0

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> NS:
        if self._fail_on is not None and self._i == self._fail_on:
            raise RuntimeError("connection reset by peer at https://llm.example sk-test-1234567890")
        try:
            text = self._chunks[self._i]
        except IndexError:
            raise StopAsyncIteration from None
        self._i += 1
        return NS(choices=[NS(delta=NS(content=text))])


def test_stream_chat_yields_text_pieces_with_chat_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_client(monkeypatch, _FakeStream([None, "净含量", "为480ml", "。"]))
    pieces = _consume("系统提示", "顾客问题")
    assert pieces == ["净含量", "为480ml", "。"]  # None 增量（role 首块）被跳过
    assert len(calls) == 1
    assert calls[0]["model"] == "fake-model"
    assert calls[0]["stream"] is True
    assert calls[0]["messages"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "顾客问题"},
    ]


def test_stream_chat_sends_session_header_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """opencode 网关硬性要求 x-opencode-session（缺则 400 MissingSessionID，
    2026-09-08 Owner 验收实测）；每问独立（0023 无多轮记忆）= 请求级新 id。"""
    calls = _install_fake_client(monkeypatch, _FakeStream(["ok"]))
    _consume()
    _consume()
    ids = [c["extra_headers"]["x-opencode-session"] for c in calls]
    assert all(isinstance(i, str) and i for i in ids)
    assert ids[0] != ids[1], "两次调用各自新 id（一问一对话）"


def test_stream_chat_wraps_create_errors_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    """建流失败（超时/连接）-> LLMUnavailable 通用文案；异常原文（可能含
    端点/请求 id）只留在 cause 链与服务端日志，不进异常消息文本。"""

    class Boom:
        async def create(self, **kwargs: Any) -> Any:
            raise RuntimeError("timeout connecting https://llm.example sk-test-1234567890")

    class Chat:
        completions = Boom()

    class Client:
        chat = Chat()

    monkeypatch.setattr(llm, "_client", Client())
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: Settings(llm_api_key="sk-test-1234567890", llm_base_url="http://localhost:9/v1", llm_model="m"),
    )
    with pytest.raises(llm.LLMUnavailable) as excinfo:
        _consume()
    assert "sk-test-1234567890" not in str(excinfo.value)
    assert "llm.example" not in str(excinfo.value)
    assert str(excinfo.value)  # 通用文案非空


def test_stream_chat_wraps_midstream_errors_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    """流中途失败同样包装（不向调用方漏原始异常类型）。"""
    calls = _install_fake_client(monkeypatch, _FakeStream(["前半"], fail_on=1))
    with pytest.raises(llm.LLMUnavailable) as excinfo:
        _consume()
    assert "sk-test-1234567890" not in str(excinfo.value)
    assert len(calls) == 1
