"""云文生图客户端（第 98 刀，ADR 0055）：素材任务的配图生成。

- OpenAI 官方包配 ``base_url``，走 OpenAI 兼容 Images API：
  ``POST {base}/images/generations``，载荷 ``{prompt, model, size,
  response_format:"b64_json"}``，取 ``data[0].b64_json`` 解码为图片字节。
  默认硅基流动（有免费 FLUX 档）；任意 OpenAI 兼容图像端点换 base 即可。
- **同步客户端**（进程级单例 + 线程锁）：调用方是同步 def 路由（素材任务建/
  重试请求内就地执行），FastAPI 丢线程池——阻塞的厂商 HTTP 与机洗回流链路
  的 ``asyncio.run`` 前提同形，不占事件循环（vlm.py 同款）。
- 密钥纪律（同 0033/93/94a）：``IMGGEN_API_KEY`` 只经 env 进程内存——不写日志、
  不进异常文案（超时/连接错误一律转通用文案）、不进任何响应。**空 key = 不建
  客户端、不发请求**：素材任务的配图步据此**诚实跳过**（任务详情标注「未配置
  IMGGEN_API_KEY，跳过」），任务本体（文案生成+质检）不受影响——与 ASR/VLM
  的 409 fail-closed 刻意不同级：配图是增值项不是任务本体（ADR 0055）。
- 生成结果按**字节魔数**复验（vlm.image_mime 同一张表）：厂商声明 png 而字节
  不是可识别图片格式 = ImggenUnavailable（不拿坏字节冒充配图）。
- 全程不重试：操作者等在同步请求里（文案 ≤20s + 质检 ≤20s + 配图 ≤60s），
  重试只会把等待翻倍；失败=配图步失败（任务照常 pending_qc，image_status
  如实记 failed，可整任务重试再生成）。
"""

import base64
import logging
import threading
from typing import Any

from openai import OpenAI

from suite_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 请求超时（秒）：文生图出图慢于 Chat（FLUX schnell 一张常在 5–20s，排队时更久）
# ——素材任务同步就地的等待面从 LLM 的 20s 放宽到 60s；前端建任务超时同步放宽。
IMGGEN_TIMEOUT_SECONDS = 60.0


class ImggenError(Exception):
    """配图失败基类（调用方按子类分派：跳过/记失败不 fail 任务）。"""


class ImggenNotConfigured(ImggenError):
    """IMGGEN_API_KEY 未配置（空凭证不建客户端、不发请求）。"""


class ImggenUnavailable(ImggenError):
    """文生图请求失败（超时/连接/响应异常/无图字节/字节不是可识别图片格式）。
    消息为通用文案，不含密钥/端点 URL；异常原文只保留在服务端日志与 cause 链。"""


# 进程级单例（同步客户端线程安全：路由跑在线程池，多个请求共享一份连接池）。
# 懒建（而非 import 期）保证空凭证进程不持有客户端；测试注入替身走
# `generate_image` 这个缝（不碰客户端）。密钥只在构造时从 settings 读入内存，
# 此后不再外流（0033）；settings 进程内缓存不变，热轮换密钥需重启（与 LLM/
# ASR/VLM 同口径）。
_client: OpenAI | None = None
_client_lock = threading.Lock()


def _new_client(settings: Settings) -> OpenAI:
    return OpenAI(
        api_key=settings.imggen_api_key,
        base_url=settings.imggen_base_url,
        timeout=IMGGEN_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers={"User-Agent": "ecommerce-ai-suite/0.1"},
    )


def _get_client() -> OpenAI:
    global _client
    settings = get_settings()
    if not settings.imggen_api_key:
        raise ImggenNotConfigured("未配置 IMGGEN_API_KEY，文生图不可用")
    with _client_lock:
        if _client is None:
            _client = _new_client(settings)
        return _client


def is_configured() -> bool:
    """文生图是否已配置（前端「生成配图」开关禁用判据与测试共用同一口径）。"""
    return bool(get_settings().imggen_api_key)


def _decode_b64_json(response: Any) -> bytes:
    """Images API 响应 → 图片字节：取 ``data[0].b64_json`` 解码并按魔数复验。

    响应形状不合（无 data/无 b64_json/坏 base64）或解码后字节不是可识别的
    png/jpeg/webp 一律 ImggenUnavailable——不拿空字节或文本字节冒充配图
    （图片字节后续要登记为 image 资产，键后缀跟魔数走）。
    """
    from suite_api.services.vlm import image_mime  # 延迟导入：单一魔数真源

    items = getattr(response, "data", None) or []
    payload = None
    for item in items:
        payload = getattr(item, "b64_json", None)
        # 第 98 刀评审补记：部分模型（如 Kwai-Kolors）忽略 response_format=b64_json，
        # 仍返回 url——此时下载 URL 字节（走 httpx 同客户端，代理/超时一致）。
        if payload is None:
            url = getattr(item, "url", None)
            if url:
                import httpx as _httpx

                resp = _httpx.get(url, timeout=60.0)
                resp.raise_for_status()
                return resp.content
        if payload:
            break
    if not payload:
        raise ImggenUnavailable("文生图服务没有返回图片字节")
    try:
        image_bytes = base64.b64decode(payload)
    except Exception as exc:  # noqa: BLE001 - 坏 base64 统一转通用文案
        raise ImggenUnavailable("文生图返回的字节不可解码") from exc
    if not image_bytes or image_mime(image_bytes) is None:
        raise ImggenUnavailable("文生图返回的字节不是可识别的 png/jpeg/webp 格式")
    return image_bytes


def generate_image(prompt: str, *, size: str) -> bytes:
    """prompt → 图片字节（同步请求；测试注入替身的缝）。

    只抛 ``ImggenError`` 子类：未配置 / 请求失败 / 无图字节 / 字节不是可识别
    图片格式。消息不含密钥/端点；不重试（调用方在同步请求里等着）。
    """
    client = _get_client()
    try:
        response = client.images.generate(
            model=get_settings().imggen_model,
            prompt=prompt,
            size=size,
            response_format="b64_json",
        )
    except ImggenError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("文生图请求失败: %s", type(exc).__name__)
        raise ImggenUnavailable("文生图服务暂时不可用") from exc
    return _decode_b64_json(response)
