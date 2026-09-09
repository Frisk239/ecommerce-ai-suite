"""厂商 Chat Completions 客户端（工程第 7 刀「厂商生成」，ADR 0028/0033）。

- `openai` 官方包配 `base_url`（spec 工程裁决：生态标准、流式与错误语义成熟），
  走 OpenAI 兼容 Chat Completions 流式接口；20s 超时、0 重试——失败由调用方
  的降级路径（answer.py 证据组装模板）兜住，不做进度条演戏。
- 密钥纪律（0033）：`LLM_API_KEY` 只经 env 进程内存——不写日志、不进异常
  消息文本（超时/连接错误一律转通用文案）、不进任何响应。
- `LLM_API_KEY` 为空 -> `LLMNotConfigured`（不建客户端、不发请求）：空凭证
  环境（CI/无 .env）的测试与本地未配置栈自动走降级，不炸外网。
- 客户端按事件循环缓存（第 16 刀 P1#1）：主循环（顾客流式）与线程一次性
  `asyncio.run` 循环（回流 QA 抽取）各用各的连接池，httpx 原语不再跨 loop。
- prompt 组装（`build_prompts`）：中文系统提示 + 结构化证据块 + 顾客问题。
  证据块 = 检索命中的切块（发布事务入索引的切块已含「字段：值」确认字段块，
  0010：confirmed 才进索引——字段值与切块文本同路，无第二条取数），各带
  「来源：A-{id}·v{N}」标注（0007 版本口径）；引用最终由服务端从检索命中
  定（模型无引用决定权），系统提示明确要求模型不输出引用编号。
- 多轮记忆（第 29 刀 feat/multi-turn）：`stream_chat` 增 history 参数——
  会话内最近轮映射为 user/assistant 消息插在 system 与本轮 user 之间；
  默认 None 时与旧两消息形状逐字节一致（既有调用与替身零改动兼容）。
"""

import asyncio
import logging
import uuid
import weakref
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from suite_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 超时即降级（0 重试）：spec 工程裁决——降级模板兜住，不在等待上叠加重试延迟
_TIMEOUT_SECONDS = 20.0
# 进 prompt 的证据条数上限（retrieve 默认 top_k=5，这里再收口：prompt 宁短而准）。
# 恒与 answer.py 的 _MAX_EVIDENCE（=2，引用上限）一致：citations 由检索命中定
# （0007），prompt 给到第 3 条而引用只取前 2 条会让「依据第 3 条作答却无引用」
# ——进 prompt 的证据必须都可被引用（评审裁决 2026-09-08）
_MAX_PROMPT_EVIDENCE = 2

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


# 按事件循环懒建缓存（第 16 刀，审计刀 3 P1#1）：官方 AsyncOpenAI 自带 httpx
# 连接池，其 anyio 原语有 loop 亲和性——进程级单例客户端被 async 路由主循环
# （顾客面 stream_chat）与同步线程池的一次性 `asyncio.run` 循环（machine_wash
# # QA 抽取）混用，混跑随机炸 "attached to a different loop"（对外表现为顾客
# 静默降级 LLMUnavailable）。改为每个运行中的 loop 各建一份连接池；一次性循环
# 结束后被 GC，WeakKeyDictionary 连带条目消失，客户端只靠 finalizer 关 socket
# （未显式 aclose，高回流吞吐下有短暂 fd churn 面——记录在案，非泄漏累积）。
# 懒建（而非 import 期）保证空凭证进程不持有客户端；测试注入替身走
# `_new_client` 构造缝。密钥只在构造时从 settings 读入内存，此后不再外流
# （0033）；settings 进程内缓存不变，热轮换密钥需重启（与修复前单例同口径）。
_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncOpenAI] = (
    weakref.WeakKeyDictionary()
)


def _new_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=_TIMEOUT_SECONDS,
        max_retries=0,
        # 网关建议客户端自带 UA 标识（opencode.ai/docs/go）
        default_headers={"User-Agent": "ecommerce-ai-suite/0.1"},
    )


def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.llm_api_key:
        raise LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用")
    # 只能在运行中的循环里取（stream_chat/complete_chat 都是 async，恒满足）；
    # 客户端与其建造循环同 loop，永不跨 loop 复用
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = _new_client(settings)
        _clients[loop] = client
    return client


def history_messages(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """多轮历史（第 29 刀 feat/multi-turn）-> Chat Completions messages 段：
    customer -> user、agent -> assistant。

    历史内容已在 conversation_memory.recent_turns 返回处统一 redact（0038
    修订纪律：厂商 prompt 必掩——收口单点，消费方不再重复掩）。None/空 ->
    []：无历史的调用 messages 与既有两消息形状逐字节一致（旧调用零改动）。
    """
    role_map = {"customer": "user", "agent": "assistant"}
    return [
        {"role": role_map[turn["role"]], "content": turn["content"]}
        for turn in history or []
    ]


async def stream_chat(
    system_prompt: str,
    user_prompt: str,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Chat Completions 流式生成，逐块 yield 文本增量。

    history（第 29 刀多轮记忆）：``[{role:"customer"|"agent", content}]`` 由
    history_messages 映射为 user/assistant 消息，插在 system 与本轮 user
    之间（指代消解所需的对话历史段）；None/空 -> 与旧形状完全一致。

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
                *history_messages(history),
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
            # opencode 网关硬性要求每对话带稳定 session 头，缺则 400
            # MissingSessionID；每问仍是一次独立请求（第 29 刀多轮记忆在
            # messages 内携带，不改变请求粒度）= 请求级新 id 即「该对话的
            # 稳定 id」，也避免跨问关联
            extra_headers={"x-opencode-session": uuid.uuid4().hex},
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


async def complete_chat(system_prompt: str, user_prompt: str) -> str:
    """聚合流式输出为完整字符串（第 12 刀回流 QA 抽取：就地同步要全文）。

    不另开非流式调用——走同一 stream_chat（同端点、同 20s/0 重试、同网关
    session 头、同 LLMError 错误契约），行为与顾客面生成完全一致。
    """
    return "".join([piece async for piece in stream_chat(system_prompt, user_prompt)])


async def complete_tool_proposal(system_prompt: str, user_prompt: str) -> str:
    """提议步专用非流式全文调用（第 37 刀，ADR 0043「模型提议、代码授权」）。

    刻意不复用 stream_chat/complete_chat：既有替身测试把 stream_chat 钉为
    「生成步唯一 LLM 等待点」的哨兵（0018 无证据不调模型、prompt 恰好一次
    等断言）——提议步若复用同一通道会把哨兵提前击穿，既有测试语义全漂。
    提议是单行格式化约定，无流式需要：非流式一次拿全文，同端点、同超时/
    0 重试、同网关 session 头、同 LLMError 错误契约（只抛 LLMError 子类，
    异常类型名进日志、密钥/端点不外泄）。空 key 同样 LLMNotConfigured——
    引擎提议步据此降级为纯检索（36 刀前行为）。"""
    client = _get_client()
    try:
        resp = await client.chat.completions.create(
            model=get_settings().llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # opencode 网关硬性要求每对话带稳定 session 头（同 stream_chat 口径）
            extra_headers={"x-opencode-session": uuid.uuid4().hex},
        )
        content = resp.choices[0].message.content if resp.choices else None
        return content or ""
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("厂商 Chat API 调用失败: %s", type(exc).__name__)
        raise LLMUnavailable("厂商模型暂时不可用") from exc


def build_prompts(hits: list[dict[str, Any]], question: str) -> tuple[str, str]:
    """组装 (system_prompt, user_prompt)：证据块各带「来源：A-{id}·v{N}」标注。

    hits=retrieve() 结果（分数降序）。prompt 只含命中切块文本与顾客问题，
    不触碰任何凭证（单测钉死：组装结果不含密钥）。

    0038 修订（第 21 刀，审计刀 4 P0 簇出口 1）：字节不动、出口必掩——证据
    chunk 已在 retrieve 返回处统一 redact（收口点见 services/retrieval.py，
    本函数不再对 chunk 重复掩）；此处单独掩顾客问句行：厂商 prompt 是进程
    边界，顾客手打的手机号/邮箱也不该裸送厂商。函数内导入避开
    machine_wash↔llm 的模块级循环（machine_wash 顶层 import llm）。
    """
    from suite_api.services.machine_wash import redact

    lines = ["已发布证据："]
    for hit in hits[:_MAX_PROMPT_EVIDENCE]:
        lines.append(f"[来源：A-{hit['asset_id']}·v{hit['version_no']}] {hit['chunk']}")
    lines.append(f"顾客问题：{redact(question)}")
    return SYSTEM_PROMPT, "\n".join(lines)
