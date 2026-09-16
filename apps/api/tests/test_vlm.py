"""云 VLM 看图出草稿单元测试（第 94a 刀，ADR 0051）：离线——不打外网、不连库。

覆盖三块：
- **字节形状纯函数**：魔数识别（png/jpeg/webp，其余诚实拒绝）、data URL 内联
  base64（唯一不暴露内部对象键的形态）、messages 的 OpenAI 视觉口径；
- **客户端契约**：无 key 不建客户端（显式钉 `_new_client` 不被调用）、
  `_get_client` 抛 VLMNotConfigured、响应空文本=失败（不静默落空串）；
- **草稿三态接线**（`machine_wash.extract_image_description`）：未配置=弃权、
  失败=弃权（不把资产扣在已接入）、成功=value+source=machine 且过 redact。
"""

import base64
from types import SimpleNamespace

import pytest

import suite_api.services.vlm as vlm
from suite_api.services.machine_wash import extract_image_description
from suite_api.services.vlm import (
    VLMNotConfigured,
    VLMUnavailable,
    build_messages,
    data_url,
    describe_image,
    image_mime,
)
from suite_api.settings import Settings

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
_JPEG = b"\xff\xd8\xff\xe0" + b"jfif-payload"
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"payload"


# ---------------------------------------------------------------- 字节形状


def test_image_mime_recognizes_three_formats_and_rejects_others() -> None:
    assert image_mime(_PNG) == "image/png"
    assert image_mime(_JPEG) == "image/jpeg"
    assert image_mime(_WEBP) == "image/webp"
    # RIFF 但非 WEBP（如 wav）不是图片；纯文本/空字节同样不是
    assert image_mime(b"RIFF\x24\x00\x00\x00WAVEfmt ") is None
    assert image_mime("产品说明：净含量 550ml".encode()) is None
    assert image_mime(b"") is None


def test_data_url_embeds_base64_and_rejects_unrecognized_bytes() -> None:
    url = data_url(_PNG)
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == _PNG  # 内联的是原字节，不是重编码
    with pytest.raises(VLMUnavailable, match="png/jpeg/webp"):
        data_url(b"not-an-image")


def test_build_messages_is_openai_vision_shape() -> None:
    messages = build_messages(data_url(_JPEG))
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    parts = messages[1]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # 组装结果不含任何凭证（prompt 是进程边界，密钥不经它出边界）
    assert "api_key" not in repr(messages)


# ---------------------------------------------------------------- 客户端契约


def test_settings_defaults_are_openai_compatible_vision_endpoint() -> None:
    settings = Settings(_env_file=None)
    assert settings.vlm_api_key == ""
    assert settings.vlm_base_url == "https://api.openai.com/v1"
    assert settings.vlm_model == "gpt-4o-mini"


def test_get_client_without_key_never_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 key=不建客户端（显式钉：`_new_client` 被调用即失败）。"""
    monkeypatch.setattr(vlm, "get_settings", lambda: Settings(vlm_api_key=""))
    monkeypatch.setattr(vlm, "_client", None)

    def _explode(settings: Settings) -> object:  # pragma: no cover - 不该被调用
        raise AssertionError("空 key 不许建客户端")

    monkeypatch.setattr(vlm, "_new_client", _explode)
    with pytest.raises(VLMNotConfigured):
        vlm._get_client()
    assert vlm.is_configured() is False


def test_is_configured_true_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vlm, "get_settings", lambda: Settings(vlm_api_key="test-key"))
    assert vlm.is_configured() is True


class _StubClient:
    """chat.completions.create 的最小替身：返回给定文本或抛异常。"""

    def __init__(self, *, content: str | None = None, error: Exception | None = None) -> None:
        self.seen: dict | None = None
        self._content = content
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):  # noqa: ANN003, ANN202 - 测试替身
        self.seen = kwargs
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


def _use_client(monkeypatch: pytest.MonkeyPatch, client: _StubClient) -> None:
    monkeypatch.setattr(vlm, "get_settings", lambda: Settings(vlm_api_key="test-key"))
    monkeypatch.setattr(vlm, "_get_client", lambda: client)


def test_describe_image_returns_trimmed_text_and_sends_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StubClient(content="  显示器侧面带可调节支架。  ")
    _use_client(monkeypatch, client)
    assert describe_image(_PNG) == "显示器侧面带可调节支架。"
    assert client.seen is not None
    assert client.seen["model"] == Settings(_env_file=None).vlm_model
    assert client.seen["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png")


def test_describe_image_empty_or_failed_is_honest_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_client(monkeypatch, _StubClient(content="   "))
    with pytest.raises(VLMUnavailable, match="没有返回描述内容"):
        describe_image(_PNG)

    _use_client(monkeypatch, _StubClient(error=RuntimeError("boom: https://api.example/v1")))
    with pytest.raises(VLMUnavailable) as excinfo:
        describe_image(_PNG)
    # 通用文案：上游异常原文（可能含端点/请求 id）不外泄给调用方
    assert "api.example" not in str(excinfo.value)


def test_describe_image_rejects_non_image_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _StubClient(content="不该被调用")
    _use_client(monkeypatch, client)
    with pytest.raises(VLMUnavailable):
        describe_image(b"hello")
    assert client.seen is None  # 字节不是图片：不发明请求


# ---------------------------------------------------------------- 草稿三态接线


def test_extract_image_description_abstains_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vlm, "get_settings", lambda: Settings(vlm_api_key=""))
    assert extract_image_description(_PNG) == {"abstained": True}


def test_extract_image_description_abstains_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败=草稿缺位（弃权），不抛——资产照常推进待人洗，人洗补写兜底。"""

    def _boom(_bytes: bytes) -> str:
        raise VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm, "describe_image", _boom)
    assert extract_image_description(_PNG) == {"abstained": True}


def test_extract_image_description_success_is_machine_draft_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "describe_image", lambda _bytes: "支架款显示器，联系电话 13812345678")
    entry = extract_image_description(_PNG)
    assert entry["source"] == "machine"  # 草稿：进 extracted，人确认才生效
    assert "13812345678" not in entry["value"]  # 打码步（ADR 0038）：出口必掩
    assert "支架" in entry["value"]
