"""第 106 刀 retrieve() 融合路径单测（全离线：mock embedding，不动真网）。

钉四件事：
- 服务侧 fuse_dense_sparse 与矩阵脚本 linear_fuse 同输入同名次（实现=实验）；
- retrieve() 融合路径的形状：向量补召回进榜 / NULL 块（无向量）保留词法资格
  （fail-open 不排除）/ 词法空手闸（lexgate：词法零命中时向量不无中生有）；
- 无 EMBED_API_KEY（conftest 强制空凭证）= 纯词法零变化：名次与词法管线
  逐位一致（fail-open 钉测）；
- 查询向量缓存：同问句同库只调一次云 API（embedding.embed_texts 计数）。

真库真 SQL（TAU 闸/评论闸下推/<=> 排序）的行为面由实现后 run_eval 对照背书
（236 条全量=矩阵胜者行逐位一致，见 rag-eval-report 第 106 刀节）。
"""

import sys
from pathlib import Path
from typing import Any

import pytest

from suite_api.services import embedding, retrieval
from suite_api.services.retrieval import (
    VECTOR_COS_FLOOR,
    VECTOR_TOP_N,
    VECTOR_WEIGHT,
    fuse_dense_sparse,
    query_vector_for,
    retrieve,
)

EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
sys.path.insert(0, str(EVAL_DIR))

import fusion_matrix as fm  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_query_vector_cache() -> Any:
    """查询向量 LRU 测试间清场（fake 键不泄漏进其他用例的缓存命中）。"""
    retrieval._query_vec_cache.clear()
    yield
    retrieval._query_vec_cache.clear()


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FusionFakeDb:
    """词法候选行 + 向量近邻行双源：按 execute 调用形态分派——候选 SQL 单参
    （select）、向量 SQL 双参（sa_text + params）。向量调用参数留档供断言。"""

    def __init__(
        self,
        lex_rows: list[tuple[int, int, str]],
        vec_rows: list[tuple[int, int, str, str, float]] | None = None,
    ) -> None:
        # 词法候选 6 元组（第 79 刀口径：verified_at/source_kind/title）
        self._lex_rows = [(a, v, c, None, "upload", None) for a, v, c in lex_rows]
        self._vec_rows = vec_rows or []
        self.vec_params: dict[str, Any] | None = None

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        if params is None:
            return _FakeResult(self._lex_rows)
        self.vec_params = params
        return _FakeResult(self._vec_rows)


def _enable_embedding(
    monkeypatch: pytest.MonkeyPatch, vector: list[float] | None = None
) -> list[int]:
    """替身 embedding：is_configured=True + embed_texts 计数（可选固定向量）。"""
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [vector if vector is not None else [0.1] * embedding.EMBEDDING_DIM]

    monkeypatch.setattr(embedding, "is_configured", lambda: True)
    monkeypatch.setattr(embedding, "embed_texts", fake_embed)
    return calls


# ---------- 服务融合纯函数：与矩阵脚本同输入同名次 ----------


def test_fuse_matches_experiment_linear_fuse() -> None:
    """实现=实验：fuse_dense_sparse 与 fm.linear_fuse 同输入产出同名次同分数。"""
    lex = [
        {"asset_id": 1, "version_no": 1, "chunk": "a", "score": 3.0},
        {"asset_id": 2, "version_no": 1, "chunk": "b", "score": 2.0},
        {"asset_id": 5, "version_no": 1, "chunk": "e", "score": 1.0},
    ]
    vec = [
        {"asset_id": 3, "version_no": 1, "chunk": "c", "cos": 0.9},
        {"asset_id": 1, "version_no": 1, "chunk": "a", "cos": 0.8},
    ]
    script = fm.linear_fuse([dict(h) for h in lex], vec, vec_weight=VECTOR_WEIGHT)
    service = fuse_dense_sparse(lex, vec)
    assert [(h["asset_id"], round(h["score"], 9)) for h in script] == [
        (h["asset_id"], round(h["score"], 9)) for h in service
    ]


def test_fuse_pure_lexical_keeps_lexical_ranking() -> None:
    """向量路缺席（None/空）= 纯词法：min-max 单调保序，名次与词法分排序一致。"""
    lex = [
        {"asset_id": 1, "version_no": 1, "chunk": "a", "score": 2.5},
        {"asset_id": 2, "version_no": 1, "chunk": "b", "score": 2.5},
        {"asset_id": 3, "version_no": 1, "chunk": "c", "score": 0.1},
    ]
    for vec in (None, []):
        out = fuse_dense_sparse([dict(h) for h in lex], vec)  # type: ignore[arg-type]
        assert [(h["asset_id"], h["chunk"]) for h in out] == [(1, "a"), (2, "b"), (3, "c")]


def test_fuse_empty_lexical_is_empty() -> None:
    """词法空手闸的纯函数面：lex 空 -> 融合空（向量不无中生有）。"""
    vec = [{"asset_id": 7, "version_no": 1, "chunk": "v", "cos": 0.95}]
    assert fuse_dense_sparse([], vec) == []


# ---------- retrieve() 融合路径（mock embedding） ----------


def test_retrieve_fusion_pulls_vector_candidate_into_top(monkeypatch: pytest.MonkeyPatch) -> None:
    """向量补召回形状：词法命中块照旧 + 向量近邻块（词法零分）进榜（w×cos）。

    词法块「钛杯容量五百毫升」归一分 1.0；向量块 cos 0.9 -> 0.5×0.9=0.45
    进第 2 位（补召回不排除词法命中——fail-open 只加分）。
    """
    _enable_embedding(monkeypatch)
    db = _FusionFakeDb(
        lex_rows=[(1, 1, "钛杯容量五百毫升")],
        vec_rows=[(2, 1, "发票的开具时限是七个工作日", "upload", 0.9)],
    )
    hits = retrieve(db, "钛杯容量多少", top_k=3)  # type: ignore[arg-type]
    assert [h["asset_id"] for h in hits[:2]] == [1, 2]
    assert abs(hits[1]["score"] - VECTOR_WEIGHT * 0.9) < 1e-9
    # 向量 SQL 参数口径：TAU 闸下推 + top-N 宽度
    assert db.vec_params is not None
    assert db.vec_params["floor"] == VECTOR_COS_FLOOR
    assert db.vec_params["n"] == VECTOR_TOP_N


def test_retrieve_keeps_null_embedding_lexical_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """NULL 块 fail-open：embedding NULL/未进向量 top-N 的词法命中块照常参与
    （cos 项 0，只靠词法分），不因无向量被排除。"""
    _enable_embedding(monkeypatch)
    db = _FusionFakeDb(
        lex_rows=[(1, 1, "钛杯容量五百毫升"), (2, 1, "钛杯容量四百毫升")],
        vec_rows=[],  # 向量路全空（全部 NULL 形态）
    )
    hits = retrieve(db, "钛杯容量多少", top_k=3)  # type: ignore[arg-type]
    assert {h["asset_id"] for h in hits} == {1, 2}


def test_retrieve_lexgate_blocks_vector_when_lexical_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """词法空手闸：词法零命中的问句（对候选无 bigram 交集）即使向量近邻非空
    也空手出——拒答语义完全由词法路决定（0018 宁缺勿滥在融合层的收口）。"""
    _enable_embedding(monkeypatch)
    db = _FusionFakeDb(
        lex_rows=[(1, 1, "钛杯容量五百毫升")],
        vec_rows=[(9, 1, "完全无关但向量近邻的块", "upload", 0.95)],
    )
    assert retrieve(db, "怎么投资理财", top_k=3) == []  # type: ignore[arg-type]


def test_retrieve_filters_review_evidence_in_vector_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """评论适用域闸对向量补召回同样生效：服务状态问句下 review_import 的向量
    近邻块不作为证据（与词法路同闸——66 刀「什么算证据」单点定义）。"""
    _enable_embedding(monkeypatch)
    db = _FusionFakeDb(
        lex_rows=[(1, 1, "退货政策支持七天无理由")],
        vec_rows=[
            (44, 1, "退货还要我自己承担运费", "review_import", 0.9),
            (2, 1, "退货运费商家承担", "upload", 0.7),
        ],
    )
    hits = retrieve(db, "退货运费多少钱", top_k=3)  # type: ignore[arg-type]
    assert 44 not in [h["asset_id"] for h in hits]
    assert 2 in [h["asset_id"] for h in hits]


def test_retrieve_without_key_is_pure_lexical_zero_drift() -> None:
    """无 key 纯词法零变化钉测（conftest 强制空 EMBED_API_KEY）：融合代码在场
    时 retrieve 名次与 79 刀词法管线（词法分×stale×亲和降序）逐位一致。"""
    rows = [
        (1, 1, "钛杯容量五百毫升"),
        (2, 1, "支持七天无理由退款"),
        (3, 1, "配送范围覆盖全国"),
    ]
    hits = retrieve(_FusionFakeDb(rows), "钛杯容量多少", top_k=3)  # type: ignore[arg-type]
    # 词法命中只有块 1（容量/钛杯 bigram）——空 key 下向量路整体缺席
    assert [h["asset_id"] for h in hits] == [1]
    # 全域零命中照旧空手（拒答口径不变）
    assert retrieve(_FusionFakeDb(rows), "怎么投资理财", top_k=3) == []  # type: ignore[arg-type]


# ---------- 查询向量缓存 ----------


def test_query_vector_cached_per_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """同问句同库只调一次云 API：LRU 缓存命中后 embed_texts 不再被调。"""
    calls = _enable_embedding(monkeypatch)
    db = _FusionFakeDb([(1, 1, "钛杯容量五百毫升")])
    first = query_vector_for(db, "钛杯容量多少")  # type: ignore[arg-type]
    again = query_vector_for(db, "钛杯容量多少")  # type: ignore[arg-type]
    assert first is not None and again is first
    assert calls == [["钛杯容量多少"]]


def test_query_vector_failure_fails_open_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """嵌入失败（EmbeddingError）-> None：检索退纯词法，异常不外抛。"""
    monkeypatch.setattr(embedding, "is_configured", lambda: True)

    def boom(texts: list[str]) -> list[list[float]]:
        raise embedding.EmbeddingUnavailable("嵌入服务暂时不可用")

    monkeypatch.setattr(embedding, "embed_texts", boom)
    db = _FusionFakeDb([(1, 1, "钛杯容量五百毫升")])
    assert query_vector_for(db, "钛杯容量多少") is None  # type: ignore[arg-type]


def test_query_vector_stale_dimension_invalidated(monkeypatch: pytest.MonkeyPatch) -> None:
    """维度自检：缓存里维度不符（换过 EMBED_MODEL 的陈旧条目）作废重算。"""
    calls = _enable_embedding(monkeypatch, vector=[0.2] * embedding.EMBEDDING_DIM)
    db = _FusionFakeDb([])
    retrieval._query_vec_cache[("<fake>", "BAAI/bge-m3", "钛杯容量多少")] = [0.1] * 8  # 错维陈旧
    vector = query_vector_for(db, "钛杯容量多少")  # type: ignore[arg-type]
    assert vector == [0.2] * embedding.EMBEDDING_DIM
    assert len(calls) == 1  # 陈旧条目没被直接采用
