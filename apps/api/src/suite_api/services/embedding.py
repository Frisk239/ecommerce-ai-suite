"""云 Embedding：切块文本 -> 语义向量（第 105 刀，向量基础设施 A1）。

链路（**只备料不动检索**）：OpenAI 兼容 ``POST {base}/embeddings``，
``{model, input: [文本, ...]}`` -> ``data[].embedding``（按 index 对位）。默认
硅基流动 ``BAAI/bge-m3``（免费档，1024 维）。产出写进
``retrieval_chunks.embedding``（迁移 0034，vector(1024) nullable）——**本刀无读
消费者**（retrieve 词法主路一行不改），106 刀才做混合检索融合。

消费面（双端同源）：
- 写端：发布事务**提交后**补写（routes/assets.publish 调 retrieval.embed_version_chunks
  ——云调用不进发布事务，失败=块照写、embedding NULL+日志，发布不被阻塞）；
- 存量：``scripts/realdata/backfill_embeddings.py`` 批量回填（幂等：只补 NULL 行）。

维度自检：``EMBEDDING_DIM = 1024``（bge-m3 口径）——每批响应逐条校验维度，不符抛
``EmbeddingMisconfigured``（配置错：换过 EMBED_MODEL 而库列是 vector(1024) 时，
写进去的向量会在回读时全灭，宁可当次失败也不落错维数据）。

密钥纪律（同 0033/LLM/ASR/VLM 四件套）：``EMBED_API_KEY`` 只经 env 进程内存——
不写日志、不进异常文案（超时/连接错误一律转通用文案）、不进任何响应。**空 key =
不建客户端、不发请求**：发布照常（embedding NULL，fail-closed 不 fail 发布），
回填脚本诚实退出。

批量纪律：单请求输入 ≤ ``EMBED_BATCH_SIZE``（64）——超过自动分批串行（SiliconFlow
等网关的单请求输入上限常见为 64/128，取 64 是各 OpenAI 兼容端点的安全公约数）；
空输入列表直接返回空（不空发请求）。
"""

import logging
import threading

from openai import OpenAI

from suite_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# bge-m3 的向量维度（迁移 0034 的 vector(1024) 同源常量；换模型须同步迁移）
EMBEDDING_DIM = 1024
# 单请求输入条数上限（超出自动分批；见模块 docstring 的取值理由）
EMBED_BATCH_SIZE = 64
# 请求超时（秒）：与 VLM 同量级——批量 64 条短文本的嵌入调用是秒级；不重试
# （写端失败=留 NULL 给回填兜底，重试只会把发布请求的等待翻倍）
EMBED_TIMEOUT_SECONDS = 30.0


class EmbeddingError(Exception):
    """嵌入失败基类（调用方按子类分派：留 NULL 补写 / 配置排查）。"""


class EmbeddingNotConfigured(EmbeddingError):
    """EMBED_API_KEY 未配置（空凭证不建客户端、不发请求）。"""


class EmbeddingUnavailable(EmbeddingError):
    """嵌入请求失败（超时/连接/响应异常）。消息为通用文案，不含密钥/端点 URL；
    异常原文只保留在服务端日志与 cause 链。"""


class EmbeddingMisconfigured(EmbeddingError):
    """维度自检不符：响应向量维度 != EMBEDDING_DIM（换了 EMBED_MODEL 而库列锁定
    vector(1024) 的配置错——如实报错不落错维数据）。"""


# 进程级单例（同步客户端线程安全：路由跑在线程池，多个请求共享一份连接池）。
# 懒建（而非 import 期）保证空凭证进程不持有客户端；测试注入替身走
# `embed_texts` 这个缝（不碰客户端）。密钥只在构造时从 settings 读入内存，
# 此后不再外流（0033）；settings 进程内缓存不变，热轮换密钥需重启（与四件套同口径）。
_client: OpenAI | None = None
_client_lock = threading.Lock()


def _new_client(settings: Settings) -> OpenAI:
    return OpenAI(
        api_key=settings.embed_api_key,
        base_url=settings.embed_base_url,
        timeout=EMBED_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers={"User-Agent": "ecommerce-ai-suite/0.1"},
    )


def _get_client() -> OpenAI:
    global _client
    settings = get_settings()
    if not settings.embed_api_key:
        raise EmbeddingNotConfigured("未配置 EMBED_API_KEY，切块嵌入不可用")
    with _client_lock:
        if _client is None:
            _client = _new_client(settings)
        return _client


def is_configured() -> bool:
    """Embedding 是否已配置（写端补写开关与回填脚本的同一判据）。"""
    return bool(get_settings().embed_api_key)


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """单请求（≤EMBED_BATCH_SIZE 条）-> 向量列表（按输入顺序对位）。

    响应按 ``index`` 归位（OpenAI 兼容口径 data[].index 是输入下标；个别网关
    乱序返回时按 index 摆正，不靠运气）。逐条维度自检，不符抛 Misconfigured。
    """
    client = _get_client()
    settings = get_settings()
    try:
        response = client.embeddings.create(
            model=settings.embed_model,
            input=texts,
        )
        raw = {int(item.index): item.embedding for item in response.data}
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("Embedding 请求失败: %s", type(exc).__name__)
        raise EmbeddingUnavailable("嵌入服务暂时不可用") from exc
    vectors: list[list[float]] = []
    for position in range(len(texts)):
        vector = raw.get(position)
        if not isinstance(vector, list) or not vector:
            raise EmbeddingUnavailable("嵌入服务没有返回该输入的向量")
        if len(vector) != EMBEDDING_DIM:
            raise EmbeddingMisconfigured(
                f"嵌入模型维度不符：响应 {len(vector)} 维，库列锁定 {EMBEDDING_DIM} 维"
                f"（检查 EMBED_MODEL 是否为 bge-m3 同维模型）"
            )
        vectors.append(vector)
    return vectors


def embed_texts(texts: list[str]) -> list[list[float]]:
    """文本列表 -> 向量列表（同序；自动分批 ≤64/请求，串行合并）。

    只抛 EmbeddingError 子类；空列表直接返回空（不空发请求）。全程不重试
    （写端失败=块照写留 NULL，回填脚本可重跑兜底）。
    """
    result: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        result.extend(_embed_batch(texts[start : start + EMBED_BATCH_SIZE]))
    return result
