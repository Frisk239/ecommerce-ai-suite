"""厂商 Chat Completions 客户端（工程第 7 刀「厂商生成」，ADR 0028/0033）。

- `openai` 官方包配 `base_url`（spec 工程裁决：生态标准、流式与错误语义成熟），
  走 OpenAI 兼容 Chat Completions 流式接口；20s 超时、0 重试——失败由调用方
  的降级路径（answer.py 证据组装模板）兜住，不做进度条演戏。
- 密钥纪律（0033）：`LLM_API_KEY` 只经 env 进程内存——不写日志、不进异常
  消息文本（超时/连接错误一律转通用文案）、不进任何响应。
- `LLM_API_KEY` 为空 -> `LLMNotConfigured`（不建客户端、不发请求）：空凭证
  环境（CI/无 .env）的测试与本地未配置栈自动走降级，不炸外网。
- prompt 组装（`build_prompts`）：中文系统提示 + 结构化证据块 + 顾客问题。
  证据块 = 检索命中的切块（发布事务入索引的切块已含「字段：值」确认字段块，
  0010：confirmed 才进索引——字段值与切块文本同路，无第二条取数），各带
  「来源：A-{id}·v{N}」标注（0007 版本口径）；引用最终由服务端从检索命中
  定（模型无引用决定权），系统提示明确要求模型不输出引用编号。
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from suite_api.settings import get_settings

logger = logging.getLogger(__name__)

# 超时即降级（0 重试）：spec 工程裁决——降级模板兜住，不在等待上叠加重试延迟
_TIMEOUT_SECONDS = 20.0
# 进 prompt 的证据条数上限（retrieve 默认 top_k=5，这里再收口：prompt 宁短而准）
_MAX_EVIDENCE = 3

SYSTEM_PROMPT = (
    "你是商家侧电商 AI 客服，回答顾客关于商品与售后的问题。\n"
    "只依据提供的已发布证据回答；证据里没有的信息不要编造，宁可说明证据未覆盖。\n"
    "回答简洁，直接给结论与关键信息，不寒暄不闲聊。\n"
    "不要输出引用编号或来源标注——引用由系统在回答之外附加。"
)


class LLMError(Exception):
    """厂商生成失败基类：调用方统一捕获走降级（证据组装模板）。"""


class LLMNotConfigured(LLMError):
    """LLM_API_KEY 未配置（0033：密钥只从 .env 读；空凭证不建客户端）。"""


class LLMUnavailable(LLMError):
    """厂商 Chat API 请求失败（未配置以外的一切：超时/连接/响应异常）。消息为
    通用文案，不含密钥/端点 URL；异常原文只保留在服务端日志与 cause 链。"""


# 模块级单例：官方 AsyncOpenAI 客户端协程安全且自带 httpx 连接池，进程内复用
# 免去每问重建 TCP/TLS；懒建（而非 import 期）保证空凭证进程不持有客户端，
# 测试也可注入替身。密钥只在构造时从 settings 读入内存，此后不再外流。
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.llm_api_key:
        raise LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用")
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _client


async def stream_chat(system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
    """Chat Completions 流式生成，逐块 yield 文本增量。

    契约：除 `LLMError` 子类外不向外抛——openai 的超时/连接/响应异常全部包装
    为 `LLMUnavailable`（通用文案）。日志只记异常类型名，不带 str(exc)（其中
    可能含 base_url/请求 id，一律不外泄）。
    """
    client = _get_client()
    try:
        stream = await client.chat.completions.create(
            model=get_settings().llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )
        async for chunk in stream:
            # 首块 delta 可能只有 role（content=None）；空增量直接跳过
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("厂商 Chat API 调用失败: %s", type(exc).__name__)
        raise LLMUnavailable("厂商模型暂时不可用") from exc


def build_prompts(hits: list[dict[str, Any]], question: str) -> tuple[str, str]:
    """组装 (system_prompt, user_prompt)：证据块各带「来源：A-{id}·v{N}」标注。

    hits=retrieve() 结果（分数降序）。prompt 只含命中切块文本与顾客问题，
    不触碰任何凭证（单测钉死：组装结果不含密钥）。
    """
    lines = ["已发布证据："]
    for hit in hits[:_MAX_EVIDENCE]:
        lines.append(f"[来源：A-{hit['asset_id']}·v{hit['version_no']}] {hit['chunk']}")
    lines.append(f"顾客问题：{question}")
    return SYSTEM_PROMPT, "\n".join(lines)
