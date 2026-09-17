"""第 106 刀实验脚本：稠密稀疏混合检索的三种融合形态矩阵。

75/79 刀同款「测量在先」：本脚本在演示库（598 块已全量回填 bge-m3 向量，
105 刀）上对 golden 236 条六分布问句复现 retrieve 词法全管线（打分/stale/
评论闸/去重/redact），叠加 pgvector 向量路（``embedding <=> :qv`` HNSW
cosine 距离），对三种融合形态 × 参数 × ef_search × 亲和应用型跑全量三指标，
是 ADR 0058 定案的证据（数字见 rag-eval-report 第 106 刀节）。

三种形态（融合纯函数在本脚本，不动 services——胜者才进 retrieve）：
- **A 级联兜底** ``cascade_fuse``：词法 top1 分 ≥ 阈值 T -> 纯词法（与现役
  retrieve 同序）；否则向量 cosine top-K 补召回——向量组（cosine 序）在前、
  词法 top-K 中未被向量覆盖的块（含 embedding NULL 块）续后。词法弱时向量
  主导、词法命中不丢（NULL 块保留词法资格）。
- **B 并联 RRF** ``rrf_fuse``：词法 top-10 + 向量 top-10 各自排名，加权 RRF
  融合：score = 1/(k+rank_lex) + w/(k+rank_vec)（k=60，Cormack 2009 的
  加权变体——词法路权重恒 1，向量路权重 w 越大向量话语权越大，w=1 即标准
  RRF）；只出现在一路的块另一路不计。
- **C 加权线性** ``linear_fuse``：词法分 per-query min-max 归一（归一域=
  该问句的词法命中全集，与 retrieve 在线可复现同口径；max==min 时命中集
  全取 1.0——唯一命中即最强词法证据）+ cosine×w 求和；向量补召回块的词法
  项为 0（无词法证据）。cosine 相似度 = 1 - pgvector 余弦距离。

共同纪律（roadmap 显式要求）：
- **NULL 块 fail-open 到词法**：向量路只加分/补召回，不排除任何块——
  embedding NULL 的块在词法路照常参与（A 的词法组/B 的词法排名/C 的归一项），
  从不因无向量被剔除；
- **评论适用域闸对向量补召回同样生效**（66 刀「什么算证据」单点定义——
  服务状态问句下 review_import 块不作为证据，与词法路同闸，四出口同语义）；
- 亲和两型（79 刀乘数 1+3*affinity 在融合管线的位置）：
  ``affinity_before``=词法分先乘亲和再融合（级联判据/RRF 排名/线性归一都
  用乘过亲和的分——即现役词法管线原样做词法路输入）；
  ``affinity_after``=词法路裸分（stale 后）融合，融合排序取 top-10 后乘
  亲和重排再截 top_k（亲和能看到融合 top-10，救得回 6..10 位的期望块）。

矩阵：
    A 级联  T ∈ {1.0, 2.0, 3.0, 5.0} × ef ∈ {40, 80, 200} × 亲和两型 ×
            TAU ∈ {0, 0.50, 0.55, 0.60, 0.65} × 闸 ∈ {open, lexgate}
    B RRF   w ∈ {0.3, 0.5, 0.7}     × ef × 两型 × TAU × 闸
    C 线性  w ∈ {0.2, 0.5, 1.0, 2.0} × ef × 两型 × TAU × 闸
    共 661 行（+ baseline 纯词法 = 107a 基线复算）。
TAU（cosine 下限闸）与 lexgate（词法空手闸）是两轮实测教训的产物：
- 无闸时全形态拒答率崩 0——向量近邻永远存在，词法零命中的 refusal 问句被
  强行灌进证据（0018 宁缺勿滥被破）；
- 全局 TAU 封顶只救回 56.7% 拒答（TAU=0.65）：实测 refusal 组向量 top1
  cos（0.501-0.754）与 cite 组期望资产（0.402+）完全重叠，无阈值可分；
- lexgate：词法命中非空才让向量路参与（只加分不无中生有）——refusal 组
  26/30 词法零命中的照旧空手拒答，cite 组（词法 recall@3 96.6）融合收益
  全保留。词法路不受 TAU/闸影响（NULL 块/词法命中照旧——fail-open 纪律）。
judge 口径与 run_eval 生产一致：融合输出截 top-3 再判定（recall@3 用足
传入列表，rerank_experiment 先例 [:TOP_K]）。

查询向量只算一次：236 问句对 ``services/embedding.embed_texts`` 批量嵌入
（4 批 API 调用），落盘 ``out/fusion-query-vectors.json`` 缓存复用（键=
sha1(模型+问句)——重跑矩阵零 API；胜者进 retrieve 时另做进程内缓存）。

用法（仓库根，连演示库；需 .env 配 EMBED_API_KEY）：
    uv run python scripts/eval/fusion_matrix.py --db postgresql://suite:suite@localhost:5433/suite
    # 追加 --out 写矩阵工件到 scripts/eval/out/106-fusion-matrix.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from run_eval import TOP_K, aggregate, format_table, judge_case
from sqlalchemy import create_engine, select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker

from suite_api.db import to_sqlalchemy_url
from suite_api.models import Asset, AssetVersion, RetrievalChunk
from suite_api.services.retrieval import (
    AFFINITY_ALPHA,
    STALE_MULTIPLIER,
    excludes_review_evidence,
    is_stale,
    query_terms,
    redact,
    score_chunk,
    stale_days,
    title_affinity,
    title_idf,
)
from suite_api.services.synonyms import apply_synonyms

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "postgresql://suite:suite@localhost:5433/suite"
DEFAULT_GOLDEN = SCRIPT_DIR / "out" / "golden_large.json"
VECTOR_CACHE = SCRIPT_DIR / "out" / "fusion-query-vectors.json"

# 向量路召回宽度（B 形态任务口径 top-10；A 补召回/C 并集同宽，单一常量）
VEC_TOP_N = 10
# RRF 平滑常数（Cormack 2009 标准值）
RRF_K = 60
# 融合输出窗：先取融合 top-10（affinity_after 的重排窗），出口统一截 top-3
FUSION_WINDOW = 10
EF_SEARCH_LEVELS = (40, 80, 200)
CASCADE_THRESHOLDS = (1.0, 2.0, 3.0, 5.0)
RRF_VEC_WEIGHTS = (0.3, 0.5, 0.7)
LINEAR_VEC_WEIGHTS = (0.2, 0.5, 1.0, 2.0)
# 向量相似度下限（cosine 闸）：cos < TAU 的向量命中不进融合——第一轮矩阵的
# 实测教训（全形态拒答率崩 0）：向量近邻永远存在，无闸时 refusal 问句（词法
# 零命中）被强行灌进证据，0018 宁缺勿滥被破坏。TAU=0 即无闸对照。档位依据
# 实测分布（refusal 组向量 top1 cos 0.501-0.754 与 cite 组期望资产 0.402+
# 完全重叠——无完美可分值，矩阵取「拒答保住线」与「改善保留线」的权衡点）。
COS_FLOORS = (0.0, 0.50, 0.55, 0.60, 0.65)
# 词法空手闸（第二轮矩阵的实测教训：全局 TAU 封顶只救回 56.7% 拒答——refusal
# 组向量近邻与正例相似度重叠，无阈值可分）。拒答崩 0 的唯一机制是「词法零
# 命中时向量无中生有」：refusal 组 26/30 词法零命中，cite 组词法命中 recall@3
# 96.6——lexgate=词法命中非空才让向量路参与（只加分不无中生有，0018 在融合
# 层的延伸）后，词法零命中的拒答照旧空手，cite 组融合收益全保留。
GATES = ("open", "lexgate")
# 红线（107a 基线）：positive recall@1 79/80、refusal 拒答率 26/30
POSITIVE_R1_FLOOR = 79 / 80
REFUSAL_RATE_FLOOR = 26 / 30

# (形态, 参数标签, 参数值)；矩阵行的展开顺序 = 形态 × 参数 × ef × 亲和 × TAU × 闸
CONFIGS: tuple[tuple[str, str, float], ...] = (
    *[("A", f"T={t}", t) for t in CASCADE_THRESHOLDS],
    *[("B", f"w={w}", w) for w in RRF_VEC_WEIGHTS],
    *[("C", f"w={w}", w) for w in LINEAR_VEC_WEIGHTS],
)
MATRIX_LABELS: tuple[str, ...] = ("baseline",) + tuple(
    f"{shape}-{param}-ef{ef}-{aff}-tau{tau:.2f}-{gate}"
    for shape, param, _value in CONFIGS
    for ef in EF_SEARCH_LEVELS
    for aff in ("before", "after")
    for tau in COS_FLOORS
    for gate in GATES
)


# ---------------------------------------------------------------- 融合纯函数（可单测）


def _key(hit: dict[str, Any]) -> tuple[int, int, str]:
    """去重键（与 retrieve 的 unique 同口径：(asset_id, version_no, chunk)）。"""
    return (hit["asset_id"], hit["version_no"], hit["chunk"])


def _stable_sorted(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """score 降序、并列按 (asset_id, chunk) 稳定（与 retrieve 尾部同款）。"""
    return sorted(hits, key=lambda h: (-h["score"], h["asset_id"], h["chunk"]))


def cascade_fuse(
    lex_hits: list[dict[str, Any]],
    vec_hits: list[dict[str, Any]],
    *,
    threshold: float,
    top_k: int = FUSION_WINDOW,
) -> list[dict[str, Any]]:
    """A 级联兜底：词法强走纯词法，词法弱向量补召回主导。

    lex_hits：词法综合分（stale[×亲和，按调用方口径]已乘）降序；
    vec_hits：cosine 降序（已过评论闸；NULL 块天然不在——词法资格由词法组保留）。
    词法 top1 ≥ threshold -> 原样返回词法 top_k（与现役 retrieve 同序）；
    否则合并：向量组（cosine 序，score 记 cosine 便于后置亲和重排）在前 +
    词法组（未被向量覆盖者，词法序）续后。词法零命中（lex_hits 空）时判据
    恒假 -> 向量组全量承担（兜底位——拒答组风险点，矩阵的 ref% 列盯此）。
    """
    if lex_hits and lex_hits[0]["score"] >= threshold:
        return lex_hits[:top_k]
    vec_keys = {_key(hit) for hit in vec_hits}
    merged: list[dict[str, Any]] = [
        {**hit, "score": hit["cos"]} for hit in sorted(vec_hits, key=lambda h: (-h["cos"], _key(h)))
    ]
    merged.extend(hit for hit in lex_hits[:top_k] if _key(hit) not in vec_keys)
    return merged[:top_k]


def rrf_fuse(
    lex_hits: list[dict[str, Any]],
    vec_hits: list[dict[str, Any]],
    *,
    vec_weight: float,
    k: int = RRF_K,
    top_k: int = FUSION_WINDOW,
    width: int = VEC_TOP_N,
) -> list[dict[str, Any]]:
    """B 并联 RRF：两路各取 top-width 排名，score = 1/(k+rank_lex) + w/(k+rank_vec)。

    rank 从 1 起；只在一路出现的块另一路不计贡献。并列按 (score 降序,
    asset_id, chunk) 稳定。返回行带 RRF 融合分（score 键）。
    """
    scores: dict[tuple[int, int, str], float] = {}
    rows: dict[tuple[int, int, str], dict[str, Any]] = {}
    for rank, hit in enumerate(lex_hits[:width], start=1):
        key = _key(hit)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        rows.setdefault(key, hit)
    for rank, hit in enumerate(vec_hits[:width], start=1):
        key = _key(hit)
        scores[key] = scores.get(key, 0.0) + vec_weight / (k + rank)
        rows.setdefault(key, hit)
    fused = [{**rows[key], "score": score} for key, score in scores.items()]
    return _stable_sorted(fused)[:top_k]


def linear_fuse(
    lex_hits: list[dict[str, Any]],
    vec_hits: list[dict[str, Any]],
    *,
    vec_weight: float,
    top_k: int = FUSION_WINDOW,
) -> list[dict[str, Any]]:
    """C 加权线性：词法分 per-query min-max 归一 + cosine×w 求和。

    归一域 = lex_hits 全集（该问句的词法命中全集，retrieve 在线同口径可复现）；
    max==min（含单块命中）时命中集全取 1.0（唯一命中即最强词法证据）。向量补
    召回块（不在词法命中集）词法项为 0。cosine 相似度 = 1 - 余弦距离。
    """
    norm: dict[tuple[int, int, str], float] = {}
    if lex_hits:
        raw = [hit["score"] for hit in lex_hits]
        lo, hi = min(raw), max(raw)
        for hit in lex_hits:
            norm[_key(hit)] = 1.0 if hi <= lo else (hit["score"] - lo) / (hi - lo)
    pool: dict[tuple[int, int, str], dict[str, Any]] = {}
    for hit in lex_hits:
        pool[_key(hit)] = {
            **hit,
            "score": norm[_key(hit)] + vec_weight * hit.get("cos", 0.0),
        }
    for hit in vec_hits:
        key = _key(hit)
        lex_term = norm.get(key, 0.0)
        pool[key] = {**hit, "score": lex_term + vec_weight * hit["cos"]}
    return _stable_sorted(list(pool.values()))[:top_k]


def apply_affinity_after(
    hits: list[dict[str, Any]],
    aff_of: dict[int, float],
    *,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:
    """亲和后置（79 刀乘数）：融合 top-10 上乘 1+AFFINITY_ALPHA*aff 重排再截 top_k。"""
    reranked = [
        {
            **hit,
            "score": hit["score"] * (1.0 + AFFINITY_ALPHA * aff_of.get(hit["asset_id"], 0.0)),
        }
        for hit in hits[:FUSION_WINDOW]
    ]
    return _stable_sorted(reranked)[:top_k]


# ---------------------------------------------------------------- 查询向量缓存


def warm_query_vectors(questions: list[str]) -> dict[str, list[float]]:
    """批量预热问句向量并落盘缓存文件（键 sha1(模型+问句) -> 向量）。

    缓存文件存在且覆盖全部问句时零 API 调用（重跑矩阵免费）。部分覆盖时只嵌
    缺失问句（embed_texts 自动分批）。维度不符的陈旧缓存条目作废重算
    （EMBED_MODEL 换过的情况——宁可重调也不落错维向量进矩阵）。
    """
    from suite_api.services import embedding
    from suite_api.settings import get_settings

    model = get_settings().embed_model
    cache: dict[str, list[float]] = {}
    if VECTOR_CACHE.exists():
        try:
            raw = json.loads(VECTOR_CACHE.read_text(encoding="utf-8"))
            cache = {
                key: value
                for key, value in raw.items()
                if isinstance(value, list) and len(value) == embedding.EMBEDDING_DIM
            }
        except ValueError:
            cache = {}
    keys = {
        question: hashlib.sha1(f"{model}\n{question}".encode()).hexdigest()
        for question in questions
    }
    missing = [question for question in questions if keys[question] not in cache]
    if missing:
        print(f"查询向量缓存 miss {len(missing)}/{len(questions)} 条，调 embed_texts 批量嵌入…")
        vectors = embedding.embed_texts(missing)
        for question, vector in zip(missing, vectors, strict=True):
            cache[keys[question]] = vector
        VECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        VECTOR_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        print(f"查询向量缓存写回 {VECTOR_CACHE}（共 {len(cache)} 条）")
    else:
        print(f"查询向量缓存全命中（{len(questions)} 条，零 API）")
    return {question: cache[keys[question]] for question in questions}


# ---------------------------------------------------------------- 候选与向量查询


def load_candidates(db: Any) -> list[dict[str, Any]]:
    """一次性拉回 retrieve 同口径的全候选行（79 刀 rerank_experiment 同款）。"""
    now = datetime.now(UTC)
    rows = db.execute(
        select(
            RetrievalChunk.asset_id,
            RetrievalChunk.version_no,
            RetrievalChunk.chunk,
            Asset.last_verified_at,
            Asset.source_kind,
            Asset.title,
        )
        .join(
            AssetVersion,
            (AssetVersion.asset_id == RetrievalChunk.asset_id)
            & (AssetVersion.version_no == RetrievalChunk.version_no),
        )
        .join(
            Asset,
            (Asset.id == AssetVersion.asset_id)
            & (Asset.current_published_version_id == AssetVersion.id)
            & (Asset.status == "published")
            & (Asset.discarded_at.is_(None)),
        )
        .order_by(RetrievalChunk.id)
        .limit(10000)
    ).all()
    days = stale_days()
    return [
        {
            "asset_id": asset_id,
            "version_no": version_no,
            "chunk": chunk,
            "stale_mult": STALE_MULTIPLIER if is_stale(verified_at, now=now, days=days) else 1.0,
            "source_kind": source_kind,
            "title": title,
        }
        for asset_id, version_no, chunk, verified_at, source_kind, title in rows
    ]


# 向量近邻查询：当前指针版 join 同口径 + embedding IS NOT NULL（NULL 块不进
# 向量路——词法资格由词法路保留，fail-open）；ORDER BY <=> 取 cosine 距离最近
# 的前 :n，SELECT 面带回 1-距离 = cosine 相似度。ef_search 是会话级参数，由
# 调用方在档位切换时 SET（见 _set_ef_search）。
_VECTOR_TOP_SQL = sa_text(
    """
    SELECT c.asset_id, c.version_no, c.chunk, a.source_kind, a.title,
           1 - (c.embedding <=> CAST(:qv AS vector)) AS cos_sim
    FROM retrieval_chunks c
    JOIN asset_versions v ON v.asset_id = c.asset_id AND v.version_no = c.version_no
    JOIN assets a ON a.id = v.asset_id
      AND a.current_published_version_id = v.id
      AND a.status = 'published'
      AND a.discarded_at IS NULL
    WHERE c.embedding IS NOT NULL
    ORDER BY c.embedding <=> CAST(:qv AS vector)
    LIMIT :n
    """
)


def vector_top(db: Any, query_vec: list[float], *, n: int = VEC_TOP_N) -> list[dict[str, Any]]:
    """问句向量 -> 当前指针版 cosine top-n（cos 相似度 = 1 - 余弦距离）。

    评论闸不在 SQL 里——调用方按问句口径统一过滤（与词法路同一处判定）。
    """
    rows = db.execute(_VECTOR_TOP_SQL, {"qv": json.dumps(query_vec), "n": n}).all()
    return [
        {
            "asset_id": asset_id,
            "version_no": version_no,
            "chunk": chunk,
            "source_kind": source_kind,
            "title": title,
            "cos": float(cos_sim),
        }
        for asset_id, version_no, chunk, source_kind, title, cos_sim in rows
    ]


def _set_ef_search(db: Any, ef: int) -> None:
    """会话级 HNSW ef_search（pgvector 0.8：SET hnsw.ef_search，连接内保持）。"""
    db.execute(sa_text(f"SET hnsw.ef_search = {int(ef)}"))  # noqa: S608 - int 常量无注入面


# ---------------------------------------------------------------- 矩阵主流程


def _redact_pool(hits: list[dict[str, Any]]) -> None:
    """chunk 出口掩码（与 retrieve 返回处同口径——compose_answer 消费）。"""
    for hit in hits:
        hit["chunk"] = redact(hit["chunk"])


def _hit_columns(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """融合结果行 -> run_eval.judge_case 消费的最小形状（asset/version/chunk/score）。"""
    return [
        {
            "asset_id": hit["asset_id"],
            "version_no": hit["version_no"],
            "chunk": hit["chunk"],
            "score": hit.get("score", 0.0),
        }
        for hit in hits
    ]


def matrix_rows(
    db: Any,
    cases: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    vectors: dict[str, list[float]],
    meta: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """跑全矩阵 -> (每配置摘要行列表, 每配置 judge 行)。

    每问句：词法命中两套（裸分/含亲和，去重降序）与全资产亲和映射只算一次；
    每 ef 档：向量 top-10 一查（含评论闸过滤 + SET ef_search）。67 配置全部
    内存复算——融合纯函数不触 DB。
    """
    from suite_api.services.answer import compose_answer

    idf = title_idf(list({c["asset_id"]: c["title"] for c in candidates}.values()))
    titles_of = {c["asset_id"]: c["title"] for c in candidates}
    lex_cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, float]]] = {}
    vec_cache: dict[int, dict[str, list[dict[str, Any]]]] = {}

    def lexical(
        question: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, float]]:
        """问句 -> (裸词法命中降序[stale 后], 含亲和词法命中降序, 资产亲和映射)。"""
        if question in lex_cache:
            return lex_cache[question]
        terms = query_terms(question) | query_terms(apply_synonyms(question))
        drop_reviews = excludes_review_evidence(question)
        scored = [
            {**c, "score": s}
            for c, s in ((c, score_chunk(terms, c["chunk"]) * c["stale_mult"]) for c in candidates)
            if s > 0.0 and not (drop_reviews and c["source_kind"] == "review_import")
        ]
        aff_of = {
            asset_id: title_affinity(terms, title, idf) for asset_id, title in titles_of.items()
        }
        with_aff = [
            {**h, "score": h["score"] * (1.0 + AFFINITY_ALPHA * aff_of.get(h["asset_id"], 0.0))}
            for h in scored
        ]

        def dedup_sorted(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
            unique: dict[tuple[int, int, str], dict[str, Any]] = {}
            for hit in hits:
                unique.setdefault(_key(hit), hit)
            return _stable_sorted(list(unique.values()))

        pair = (dedup_sorted(scored), dedup_sorted(with_aff), aff_of)
        _redact_pool(pair[0])
        _redact_pool(pair[1])
        lex_cache[question] = pair
        return pair

    def nearest(ef: int, question: str) -> list[dict[str, Any]]:
        """(ef, 问句) -> 向量 top-10（评论闸过滤 + redact；查询向量缺失=空）。"""
        if question in vec_cache.setdefault(ef, {}):
            return vec_cache[ef][question]
        vector = vectors.get(question)
        hits: list[dict[str, Any]] = []
        if vector is not None:
            _set_ef_search(db, ef)
            raw = vector_top(db, vector)
            drop_reviews = excludes_review_evidence(question)
            hits = [h for h in raw if not (drop_reviews and h["source_kind"] == "review_import")]
            _redact_pool(hits)
        vec_cache[ef][question] = hits
        return hits

    rows_by_config: dict[str, list[dict[str, Any]]] = {label: [] for label in MATRIX_LABELS}
    for case in cases:
        question = case["question"]
        bare_hits, aff_hits, aff_of = lexical(question)
        # baseline = 现役词法管线（含亲和乘数，与 retrieve 逐位同序；judge 前截
        # TOP_K=3——run_eval 生产口径 retrieve(top_k=3)，recall@3 用足传入列表）
        base_hits = _hit_columns(aff_hits[:TOP_K])
        base_composed = compose_answer(base_hits, meta)
        rows_by_config["baseline"].append(judge_case(case, base_hits, base_composed.kind))
        for ef in EF_SEARCH_LEVELS:
            vec_all = nearest(ef, question)
            for shape, param, value in CONFIGS:
                for aff in ("before", "after"):
                    lex = aff_hits if aff == "before" else bare_hits
                    for tau in COS_FLOORS:
                        for gate in GATES:
                            if gate == "lexgate" and not lex:
                                # 词法空手闸：词法零命中时向量不无中生有——
                                # 空手出（拒答语义保持，0018 宁缺勿滥）
                                fused: list[dict[str, Any]] = []
                            else:
                                vec_hits = [h for h in vec_all if h["cos"] >= tau]
                                if shape == "A":
                                    fused = cascade_fuse(lex, vec_hits, threshold=float(value))
                                elif shape == "B":
                                    fused = rrf_fuse(lex, vec_hits, vec_weight=float(value))
                                else:
                                    fused = linear_fuse(lex, vec_hits, vec_weight=float(value))
                                if aff == "after":
                                    fused = apply_affinity_after(fused, aff_of, top_k=TOP_K)
                                else:
                                    fused = fused[:TOP_K]
                            hits = _hit_columns(fused)
                            composed = compose_answer(hits, meta)
                            label = f"{shape}-{param}-ef{ef}-{aff}-tau{tau:.2f}-{gate}"
                            rows_by_config[label].append(judge_case(case, hits, composed.kind))

    summary: list[dict[str, Any]] = []
    for label in MATRIX_LABELS:
        rows = rows_by_config[label]
        agg = aggregate(rows)
        pos_r1 = agg["positive"]["recall1"]
        ref_rate = agg["refusal"]["refusal_rate"]
        summary.append(
            {
                "config": label,
                "pos_r1": pos_r1,
                "refusal": ref_rate,
                "overall_r1": agg["overall"]["recall1"],
                "redline": bool(
                    pos_r1 is not None
                    and pos_r1 >= POSITIVE_R1_FLOOR - 1e-9
                    and ref_rate is not None
                    and ref_rate >= REFUSAL_RATE_FLOOR - 1e-9
                ),
                "agg": agg,
                "rows": rows,
            }
        )
    return summary, rows_by_config


def format_matrix(summary: list[dict[str, Any]]) -> str:
    """矩阵摘要表：config | pos@1 par@1 conf@1 oov@1 sneg@1 ref% | ovr@1 ovr@3 MRR | 红线。"""
    header = (
        f"{'config':<24}{'pos@1':>7}{'par@1':>7}{'conf@1':>8}{'oov@1':>7}{'sneg@1':>8}"
        f"{'ref%':>7}{'ovr@1':>7}{'ovr@3':>7}{'MRR':>8}{'红线':>8}"
    )
    lines = [header]

    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value * 100:.1f}"

    for item in summary:
        agg = item["agg"]
        lines.append(
            f"{item['config']:<24}"
            f"{pct(agg['positive']['recall1']):>7}"
            f"{pct(agg['paraphrase']['recall1']):>7}"
            f"{pct(agg['confusion']['recall1']):>8}"
            f"{pct(agg['oov_syn']['recall1']):>7}"
            f"{pct(agg['sem_neg']['recall1']):>8}"
            f"{pct(agg['refusal']['refusal_rate']):>7}"
            f"{pct(agg['overall']['recall1']):>7}"
            f"{pct(agg['overall']['recall3']):>7}"
            f"{agg['overall']['mrr']:>8.4f}"
            f"{'OK' if item['redline'] else 'BROKEN':>8}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="稠密稀疏融合形态矩阵（第 106 刀）")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--out", type=Path, default=None, help="矩阵工件写入路径（全表+逐配置六分布详表）"
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="stdout 追加每个配置的六分布全量表（format_table；默认只打摘要矩阵）",
    )
    args = parser.parse_args(argv)

    from suite_api.services import embedding

    if not embedding.is_configured():
        print("未配置 EMBED_API_KEY（.env）——矩阵需要真查询向量，先配置再跑")
        return 2

    cases = json.loads(args.golden.read_text(encoding="utf-8"))
    vectors = warm_query_vectors([case["question"] for case in cases])

    engine = create_engine(to_sqlalchemy_url(args.db))
    try:
        with sessionmaker(bind=engine)() as db:
            candidates = load_candidates(db)
            meta = {
                asset.id: {"kind": asset.kind, "title": asset.title}
                for asset in db.scalars(
                    select(Asset).where(Asset.current_published_version_id.isnot(None))
                )
            }
            summary, rows_by_config = matrix_rows(db, cases, candidates, vectors, meta)
    finally:
        engine.dispose()

    n_assets = len({c["asset_id"] for c in candidates})
    print(
        f"golden：{args.golden}（{len(cases)} 条）  候选资产 {n_assets} 个  "
        f"候选块 {len(candidates)}  向量路 top-{VEC_TOP_N}  检索 top-{TOP_K}"
    )
    print(format_matrix(summary))

    baseline_r1 = summary[0]["overall_r1"]
    ok = [item for item in summary if item["redline"]]
    if ok:
        best = max(ok, key=lambda item: (item["overall_r1"], item["agg"]["overall"]["mrr"]))
        print(
            f"\n保红线配置 {len(ok)}/{len(summary) - 1}（baseline 除外）；"
            f"红线内 overall@1 最高：{best['config']}（{best['overall_r1'] * 100:.1f}%）"
        )
    else:
        closest = max(
            (item for item in summary[1:]),
            key=lambda item: (item["pos_r1"] or 0.0, item["overall_r1"]),
        )
        print(
            f"\n无融合配置同时保住正例 98.8 与拒答 86.7；正例@1 最高且 overall 最好的："
            f"{closest['config']}（pos@1 {closest['pos_r1'] * 100:.1f}% / ovr@1 "
            f"{closest['overall_r1'] * 100:.1f}%）——取舍进报告"
        )

    if args.detail:
        for item in summary:
            print(f"\n=== {item['config']} ===")
            print(format_table(item["agg"], judge_on=False))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            f"# 第 106 刀融合矩阵（{datetime.now(UTC).date().isoformat()}，golden {len(cases)} 条）",
            "",
            format_matrix(summary),
            "",
            f"baseline overall@1 = {baseline_r1 * 100:.1f}%",
            "",
        ]
        for item in summary:
            parts.append(f"=== {item['config']} ===")
            parts.append(format_table(item["agg"], judge_on=False))
            parts.append("")
        args.out.write_text("\n".join(parts), encoding="utf-8")
        print(f"矩阵工件 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
