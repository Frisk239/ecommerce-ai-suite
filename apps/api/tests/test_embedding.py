"""云 Embedding 单元测试（第 105 刀，向量基础设施 A1）：离线——不打外网、不连库。

覆盖三块（四件套同款结构）：
- **客户端契约**：无 key 不建客户端（显式钉 `_new_client` 不被调用）、
  `_get_client` 抛 EmbeddingNotConfigured、异常转通用文案（凭证/端点不外泄）；
- **批量纪律**：空输入零请求；≤64 条单请求；>64 自动分批串行；响应按 index
  归位（网关乱序返回不靠运气）；
- **维度自检**：1024 维过、非 1024 抛 EmbeddingMisconfigured（库列锁
  vector(1024)，错维数据宁可当次失败也不落库）。
"""

from types import SimpleNamespace

import pytest

import suite_api.services.embedding as embedding_service
from suite_api.services.embedding import (
    EMBED_BATCH_SIZE,
    EMBEDDING_DIM,
    EmbeddingMisconfigured,
    EmbeddingNotConfigured,
    EmbeddingUnavailable,
    embed_texts,
)
from suite_api.settings import Settings


class _FakeEmbeddings:
    """替身 client：按构造给的向量工厂出 data，记录每请求 input 长度。"""

    def __init__(self, vectors_for: list[list[float]], calls: list[int]) -> None:
        self._vectors_for = vectors_for
        self._calls = calls

    def create(self, *, model: str, input: list[str]) -> SimpleNamespace:  # noqa: A002
        self._calls.append(len(input))
        # 按 index 乱序返回（3 7 1 2 0 …）：embed 侧必须按 index 摆正
        order = sorted(range(len(input)), key=lambda i: (i * 7) % max(len(input), 1))
        data = [SimpleNamespace(index=i, embedding=self._vectors_for(len(input))) for i in order]
        return SimpleNamespace(data=data)


def _fake_client(monkeypatch: pytest.MonkeyPatch, vectors_for, calls: list[int]) -> None:
    monkeypatch.setattr(
        embedding_service,
        "_get_client",
        lambda: SimpleNamespace(embeddings=_FakeEmbeddings(vectors_for, calls)),
    )
    monkeypatch.setattr(
        embedding_service, "get_settings", lambda: Settings(embed_api_key="test-key")
    )


def _dim(dim: int) -> list[float]:
    return [0.5] * dim


# ---------------------------------------------------------------- 客户端契约


def test_no_key_never_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_service, "_client", None)
    built: list[Settings] = []
    monkeypatch.setattr(embedding_service, "_new_client", lambda s: built.append(s))
    settings = Settings(embed_api_key="")
    monkeypatch.setattr(embedding_service, "get_settings", lambda: settings)
    assert embedding_service.is_configured() is False
    with pytest.raises(EmbeddingNotConfigured, match="EMBED_API_KEY"):
        embedding_service._get_client()
    assert built == []  # 空凭证不建客户端（懒建保证空 key 进程不持有客户端）


def test_request_failure_becomes_generic_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def _boom(**kwargs: object) -> object:
        raise RuntimeError("boom: api.siliconflow.cn timeout with key sk-xxx")

    monkeypatch.setattr(
        embedding_service,
        "_get_client",
        lambda: SimpleNamespace(embeddings=SimpleNamespace(create=_boom)),
    )
    monkeypatch.setattr(
        embedding_service, "get_settings", lambda: Settings(embed_api_key="test-key")
    )
    del calls
    with pytest.raises(EmbeddingUnavailable, match="嵌入服务暂时不可用") as excinfo:
        embed_texts(["净含量：550毫升"])
    # 通用文案不含端点/密钥（0033 纪律）；异常原文只在 cause 链
    assert "siliconflow" not in str(excinfo.value)
    assert "sk-xxx" not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


# ---------------------------------------------------------------- 批量纪律


def test_empty_input_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    _fake_client(monkeypatch, lambda n: _dim(EMBEDDING_DIM), calls)
    assert embed_texts([]) == []
    assert calls == []  # 空列表不空发请求


def test_single_batch_up_to_64_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    _fake_client(monkeypatch, lambda n: _dim(EMBEDDING_DIM), calls)
    texts = [f"块 {i}" for i in range(EMBED_BATCH_SIZE)]
    vectors = embed_texts(texts)
    assert calls == [EMBED_BATCH_SIZE]  # 恰 64 条 = 单请求
    assert len(vectors) == EMBED_BATCH_SIZE
    assert all(len(v) == EMBEDDING_DIM for v in vectors)


def test_over_64_splits_into_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    _fake_client(monkeypatch, lambda n: _dim(EMBEDDING_DIM), calls)
    vectors = embed_texts([f"块 {i}" for i in range(EMBED_BATCH_SIZE + 1)])
    assert calls == [EMBED_BATCH_SIZE, 1]  # 65 条 = 64 + 1 两批串行
    assert len(vectors) == EMBED_BATCH_SIZE + 1


# ---------------------------------------------------------------- 维度自检


def test_dimension_check_rejects_wrong_model_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    _fake_client(monkeypatch, lambda n: _dim(768), calls)  # 换成 768 维模型
    with pytest.raises(EmbeddingMisconfigured, match="768"):
        embed_texts(["品牌：雀巢"])


def test_missing_input_index_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """响应缺了某条输入的向量（网关截断/坏行）= 诚实失败，不产出缺位列表。"""

    def _drop_one(**kwargs: object) -> object:
        data = [
            SimpleNamespace(index=i, embedding=_dim(EMBEDDING_DIM)) for i in range(1, 3)
        ]  # 输入 3 条只回 index 1、2
        return SimpleNamespace(data=data)

    monkeypatch.setattr(
        embedding_service,
        "_get_client",
        lambda: SimpleNamespace(embeddings=SimpleNamespace(create=_drop_one)),
    )
    monkeypatch.setattr(
        embedding_service, "get_settings", lambda: Settings(embed_api_key="test-key")
    )
    with pytest.raises(EmbeddingUnavailable, match="没有返回该输入的向量"):
        embed_texts(["a", "b", "c"])
