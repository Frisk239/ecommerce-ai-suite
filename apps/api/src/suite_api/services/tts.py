"""云 TTS 口播客户端（第 98b 刀，ADR 0056）：内容成片预览的口播音轨。

- OpenAI 官方包配 ``base_url``，走 OpenAI 兼容 Speech API：
  ``POST {base}/audio/speech``，载荷 ``{model, input, voice}``，响应体即音频
  字节。默认硅基流动（CosyVoice2 有免费档）；任意 OpenAI 兼容语音端点换
  base 即可。
- **同步客户端**（进程级单例 + 线程锁）：调用方是同步 def 路由（内容成片
  plan 请求内就地合成），FastAPI 丢线程池——阻塞的厂商 HTTP 与素材任务/
  机洗回流的 ``asyncio.run`` 前提同形，不占事件循环（imggen/asr 同款）。
- 密钥纪律（同 0033/93/94a/98）：``TTS_API_KEY`` 只经 env 进程内存——不写
  日志、不进异常文案（超时/连接错误一律转通用文案）、不进任何响应。**空
  key = 不建客户端、不发请求**：成片引擎据此**诚实跳过口播**（预览成片
  无音轨，任务详情标注「TTS 未配置，预览无声」）——与 ASR/VLM 的 409
  fail-closed 刻意不同级：口播是增值项不是成片本体（同 IMGGEN 的配图步，
  ADR 0055/0056 同型分层）。
- **voice 用厂商预置音色**（settings.tts_voice，非克隆输入）——ADR 0056
  红线④：数字人/声音克隆 Out，本模块的请求形状天然不携带任何参考音频。
- 生成结果按**字节魔数**复验：mp3（ID3/帧同步）/wav（RIFF）/ogg/m4a（ftyp）
  之外的字节不冒充音频（后续要进 ffmpeg 混流，坏字节只会把整次合成带崩）。
- 全程不重试：操作者等在同步请求里（选材+TTS+ffmpeg 合成预算合计 ~120s），
  重试只会把等待翻倍；失败=预览无声（with_tts=false 如实记，可整任务重发）。
"""

import logging
import threading
from typing import Any

from openai import OpenAI

from suite_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 请求超时（秒）：整段口播一次合成（15-60s 语音），给 60s 上限（同 IMGGEN 档）
TTS_TIMEOUT_SECONDS = 60.0


class TTSError(Exception):
    """TTS 失败基类（调用方按子类分派：跳过/记失败不 fail 任务）。"""


class TTSNotConfigured(TTSError):
    """TTS_API_KEY 未配置（空凭证不建客户端、不发请求）。"""


class TTSUnavailable(TTSError):
    """TTS 请求失败（超时/连接/响应异常/无音频字节/字节不是可识别音频格式）。
    消息为通用文案，不含密钥/端点 URL；异常原文只保留在服务端日志与 cause 链。"""


# 进程级单例（同步客户端线程安全：路由跑在线程池，多个请求共享一份连接池）。
# 懒建（而非 import 期）保证空凭证进程不持有客户端；测试注入替身走
# `synthesize_speech` 这个缝（不碰客户端）。密钥只在构造时从 settings 读入内存，
# 此后不再外流（0033）；settings 进程内缓存不变，热轮换密钥需重启（与 LLM/
# ASR/VLM/IMGGEN 同口径）。
_client: OpenAI | None = None
_client_lock = threading.Lock()


def _new_client(settings: Settings) -> OpenAI:
    return OpenAI(
        api_key=settings.tts_api_key,
        base_url=settings.tts_base_url,
        timeout=TTS_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers={"User-Agent": "ecommerce-ai-suite/0.1"},
    )


def _get_client() -> OpenAI:
    global _client
    settings = get_settings()
    if not settings.tts_api_key:
        raise TTSNotConfigured("未配置 TTS_API_KEY，口播合成不可用")
    with _client_lock:
        if _client is None:
            _client = _new_client(settings)
        return _client


def is_configured() -> bool:
    """TTS 是否已配置（前端口播提示与测试共用同一判据）。"""
    return bool(get_settings().tts_api_key)


# 音频字节魔数（轻嗅探）：mp3 的 ID3 头或帧同步（0xFF 0xEx）、wav 的 RIFF、
# ogg 的 OggS、mp4/m4a 的 ftyp。字节真相以 ffmpeg 混流为准——这里只挡
# 「明显不是音频」的响应（如 JSON 错误体）。
_AUDIO_MAGICS = (b"ID3", b"RIFF", b"OggS")  # 头部魔数
# m4a/mp4 的 ftyp 盒在偏移 4（评审修：头部 startswith 永不命中）
_AUDIO_MAGICS_OFFSET4 = (b"ftyp",)


def _looks_like_audio(data: bytes) -> bool:
    if not data:
        return False
    if any(data.startswith(magic) for magic in _AUDIO_MAGICS) or (
        len(data) >= 8 and data[4:8] in _AUDIO_MAGICS_OFFSET4
    ):
        return True
    # 裸 mp3 帧：11 位同步字（0xFF 后高 3 位为 111）
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _speech_bytes(response: Any) -> bytes:
    """Speech API 响应 → 音频字节：取 ``.content`` 并按魔数复验。

    兼容 ``.parse()``（SDK 较新版本返回 HttpxBinaryResponseContent）与已解出
    的 bytes 两种形状；空字节或不是可识别音频 = TTSUnavailable——不拿 JSON
    错误体冒充口播（后续 ffmpeg 混流会把整次合成带崩）。
    """
    data = getattr(response, "content", response)
    if not isinstance(data, (bytes, bytearray)):
        data = getattr(response, "read", lambda: b"")()
    if not isinstance(data, (bytes, bytearray)) or not _looks_like_audio(bytes(data)):
        raise TTSUnavailable("TTS 服务没有返回可识别的音频字节")
    return bytes(data)


def synthesize_speech(text: str) -> bytes:
    """口播文本 → 音频字节（同步请求；测试注入替身的缝）。

    只抛 ``TTSError`` 子类：未配置 / 请求失败 / 无音频字节 / 字节不是可识别
    音频格式。消息不含密钥/端点；不重试（调用方在同步请求里等着）。
    """
    client = _get_client()
    settings = get_settings()
    try:
        response = client.audio.speech.create(
            model=settings.tts_model,
            input=text,
            voice=settings.tts_voice,
        )
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("TTS 请求失败: %s", type(exc).__name__)
        raise TTSUnavailable("TTS 服务暂时不可用") from exc
    return _speech_bytes(response)
