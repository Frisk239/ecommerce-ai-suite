"""云 VLM 看图出「图片描述」草稿（第 94a 刀，ADR 0051）。

链路（**全程同步**：调用方是同步 def 路由，FastAPI 丢线程池——阻塞的厂商 HTTP
与切片真切的 ffmpeg 同形，不占事件循环；图片上传登记路由因此改成同步 def，
文档处理与既有 CSV 批量导入同款）：

1. **图片字节 → data URL**：``data:image/png;base64,...`` 内联。不用外网可取
   的图片地址——对象存储的键是内部路径，厂商读不到；内联是唯一不引入「先把
   图片传公网再让模型取」这条外流面的形态。
2. **OpenAI 兼容 chat/completions**：messages 的 user 内容为
   ``[{"type": "text", ...}, {"type": "image_url", "image_url": {"url": data_url}}]``
   （OpenAI 视觉口径；DashScope/qwen-vl 等同形可换 base）。
3. **只出草稿**：返回的文本由 ``machine_wash.extract_image_description``
   过 ``redact`` 后写进 ``extracted_fields["图片描述"]``——**机洗草稿，人确认
   才生效**（0010：confirmed 才进索引）。本模块不做任何落库。

密钥纪律（同 0033/93 刀）：``VLM_API_KEY`` 只经 env 进程内存——不写日志、不进
异常文案（超时/连接错误一律转通用文案）、不进任何响应。**空 key = 不建客户端、
不发请求**：无 key 环境上传图片不出草稿，纯人洗补写兜底（fail-诚实，不是假
成功也不是静默降级）。
"""

import base64
import logging
import threading
from typing import Any

from openai import OpenAI

from suite_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 请求超时（秒）：与机洗/回流的 LLM 等待面同纪律（≤20s）——图片登记是同步请求，
# 操作者等在请求里，超时即弃权出无草稿，不重试（重试只把等待翻倍）。
VLM_TIMEOUT_SECONDS = 20.0

# 描述草稿的提示词：这是**检索文本面**的生成，要求「看得见的才写」——厂商模型
# 没有出处可核（图片本身就是出处），但编造外观细节会让人洗确认变成走过场。
SYSTEM_PROMPT = (
    "你是电商商品图片的看图助手，为商品图写一段用于顾客检索的描述草稿。\n"
    "只描述图片里确实看得见的内容（商品形态、颜色、结构、配件、可读文字），"
    "不要编造尺寸、材质、价格等图片里没有的信息。\n"
    "用一句到三句中文陈述，直接给描述，不要寒暄、不要分点、不要写结论。"
)

# 图片魔数（键后缀与 data URL 的 MIME 都「跟字节走」，不信调用方报的类型）：
# png 与 jpeg 看前缀即可；webp 是 RIFF 容器，要另验第 8-12 字节（见 image_mime）。
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class VLMError(Exception):
    """看图失败基类（调用方按子类分派：弃权出无草稿）。"""


class VLMNotConfigured(VLMError):
    """VLM_API_KEY 未配置（空凭证不建客户端、不发请求）。"""


class VLMUnavailable(VLMError):
    """VLM 请求失败（超时/连接/响应异常/字节不是可识别的图片格式）。消息为通用
    文案，不含密钥/端点 URL；异常原文只保留在服务端日志与 cause 链。"""


# 进程级单例（同步客户端线程安全：路由跑在线程池，多个请求共享一份连接池）。
# 懒建（而非 import 期）保证空凭证进程不持有客户端；测试注入替身走
# `describe_image` 这个缝（不碰客户端）。密钥只在构造时从 settings 读入内存，
# 此后不再外流（0033）；settings 进程内缓存不变，热轮换密钥需重启（与 LLM/ASR 同口径）。
_client: OpenAI | None = None
_client_lock = threading.Lock()


def _new_client(settings: Settings) -> OpenAI:
    return OpenAI(
        api_key=settings.vlm_api_key,
        base_url=settings.vlm_base_url,
        timeout=VLM_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers={"User-Agent": "ecommerce-ai-suite/0.1"},
    )


def _get_client() -> OpenAI:
    global _client
    settings = get_settings()
    if not settings.vlm_api_key:
        raise VLMNotConfigured("未配置 VLM_API_KEY，看图出草稿不可用")
    with _client_lock:
        if _client is None:
            _client = _new_client(settings)
        return _client


def is_configured() -> bool:
    """VLM 是否已配置（前端提示与测试判据共用同一口径）。"""
    return bool(get_settings().vlm_api_key)


def image_mime(data: bytes) -> str | None:
    """字节魔数 → MIME（png/jpeg/webp）；不是这三种返回 None。

    对象键后缀与 data URL 都从这里取（第 46 刀裁决 4「扩展名跟实际字节走」
    的同口径）——上传端报的 content_type 只做入口闸，不做真相。
    """
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def data_url(image_bytes: bytes) -> str:
    """图片字节 → ``data:<mime>;base64,...``（不认识的格式抛 VLMUnavailable）。

    base64 内联是唯一不把内部对象键暴露给厂商的形态（见模块 docstring）。
    """
    mime = image_mime(image_bytes)
    if mime is None:
        raise VLMUnavailable("图片字节不是可识别的 png/jpeg/webp 格式")
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def build_messages(data_url_value: str) -> list[dict[str, Any]]:
    """组装 chat/completions messages（OpenAI 视觉口径，纯函数便于单测钉形状）。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请为这张商品图写描述草稿。"},
                {"type": "image_url", "image_url": {"url": data_url_value}},
            ],
        },
    ]


def chat_with_image(system_prompt: str, user_prompt: str, image_bytes: bytes) -> str:
    """自定提示的视觉调用（第 94c 刀洗帧打分复用同一客户端与密钥纪律）。

    与 ``describe_image`` 共用进程级单例、超时与「空输出按失败」口径，只是
    system/user 文本由调用方给——打分（帧好不好）与描述（图里是什么）是两个
    任务，不该共用一句 prompt。异常口径同 ``describe_image``：只抛 VLMError
    子类，消息不含密钥/端点；不重试。
    """
    client = _get_client()
    payload = data_url(image_bytes)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": payload}},
            ],
        },
    ]
    try:
        response = client.chat.completions.create(
            model=get_settings().vlm_model,
            messages=messages,
        )
        content = response.choices[0].message.content if response.choices else None
    except VLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("VLM 请求失败: %s", type(exc).__name__)
        raise VLMUnavailable("看图服务暂时不可用") from exc
    text = (content or "").strip()
    if not text:
        raise VLMUnavailable("看图服务没有返回内容")
    return text


def describe_image(image_bytes: bytes) -> str:
    """图片字节 → 描述草稿文本（空/空白输出按失败处理，不静默落空串）。

    只抛 VLMError 子类：未配置 / 请求失败 / 字节不是可识别图片格式 / 模型返回
    空文本。全程不重试（操作者等在同步请求里；失败=无草稿，人洗补写兜底）。
    """
    client = _get_client()
    payload = data_url(image_bytes)
    try:
        response = client.chat.completions.create(
            model=get_settings().vlm_model,
            messages=build_messages(payload),
        )
        content = response.choices[0].message.content if response.choices else None
    except VLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("VLM 请求失败: %s", type(exc).__name__)
        raise VLMUnavailable("看图服务暂时不可用") from exc
    text = (content or "").strip()
    if not text:
        raise VLMUnavailable("看图服务没有返回描述内容")
    return text
