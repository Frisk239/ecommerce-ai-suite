"""第 106 刀融合矩阵纯函数离线单测（cascade 边界 / RRF 排名融合 / 线性归一 /
亲和后置）——脚本内函数导入测试（先例 test_rag_eval_tools：sys.path 直插
scripts/eval），不连 DB、不调云。

钉的是 scripts/eval/fusion_matrix.py 的三个融合纯函数与亲和后置重排的
**形态语义**（矩阵定案的口径基础）；服务侧实现（services/retrieval.
fuse_dense_sparse）与脚本 linear_fuse 的一致性由 test_fusion_retrieval
钉（同一输入同一名次）。
"""

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
sys.path.insert(0, str(EVAL_DIR))

import fusion_matrix as fm  # noqa: E402


def _lex(asset_id: int, score: float, chunk: str = "c") -> dict:
    return {"asset_id": asset_id, "version_no": 1, "chunk": chunk, "score": score}


def _vec(asset_id: int, cos: float, chunk: str = "c") -> dict:
    return {"asset_id": asset_id, "version_no": 1, "chunk": chunk, "cos": cos}


# ---------- A 级联兜底：阈值边界与补召回形状 ----------


def test_cascade_top1_at_threshold_is_pure_lexical() -> None:
    """边界：词法 top1 分**恰等于**阈值 -> 判据 ≥ 成立，纯词法出（与现役同序）。"""
    lex = [_lex(1, 3.0), _lex(2, 2.0)]
    vec = [_vec(9, 0.9)]
    hits = fm.cascade_fuse(lex, vec, threshold=3.0, top_k=5)
    assert [h["asset_id"] for h in hits] == [1, 2]


def test_cascade_below_threshold_vector_leads_lexical_follows() -> None:
    """top1 < 阈值 -> 向量组（cosine 序）在前、词法组未覆盖块续后（NULL 块保留）。"""
    lex = [_lex(1, 2.9, "a"), _lex(2, 0.5, "b")]
    vec = [_vec(3, 0.8, "c"), _vec(1, 0.7, "a")]
    hits = fm.cascade_fuse(lex, vec, threshold=3.0, top_k=5)
    # 块 1 两路都召回：只出现一次（向量组位）；块 2 纯词法：续在向量组后
    assert [h["asset_id"] for h in hits] == [3, 1, 2]
    # 向量组分数记 cosine（affinity_after 重排的输入域）
    assert hits[0]["score"] == 0.8 and hits[1]["score"] == 0.7


def test_cascade_empty_lexical_vector_fully_carries() -> None:
    """词法零命中：判据恒假，向量组全量承担（兜底位——拒答风险的形态根源，
    生产实现由 lexgate 收口，见 test_fusion_retrieval）。"""
    vec = [_vec(3, 0.8, "c"), _vec(4, 0.9, "d")]
    hits = fm.cascade_fuse([], vec, threshold=1.0, top_k=5)
    assert [h["asset_id"] for h in hits] == [4, 3]


# ---------- B 并联 RRF：排名融合 ----------


def test_rrf_combines_ranks_with_hand_computed_score() -> None:
    """手算钉分：块 1 = 词法 rank1 + 向量 rank2（w=0.5）= 1/61 + 0.5/62。"""
    lex = [_lex(1, 9.0, "a"), _lex(2, 8.0, "b")]
    vec = [_vec(3, 0.9, "c"), _vec(1, 0.8, "a")]
    hits = fm.rrf_fuse(lex, vec, vec_weight=0.5, top_k=5)
    by_id = {h["asset_id"]: h["score"] for h in hits}
    assert abs(by_id[1] - (1 / 61 + 0.5 / 62)) < 1e-9
    assert abs(by_id[2] - 1 / 62) < 1e-9  # 只在词法路（rank2）
    assert abs(by_id[3] - 0.5 / 61) < 1e-9  # 只在向量路（rank1）
    assert [h["asset_id"] for h in hits] == [1, 2, 3]  # 融合分降序


def test_rrf_higher_vector_weight_promotes_dense_rank1() -> None:
    """向量权重升 -> 纯向量 rank1 块（词法零分）越过词法 rank2 独占块。

    w=5 时块 3（向量 rank1：5/61≈0.0820）仍低于块 1（两路相加 1/61+5/62
    ≈0.0970）但越过块 2（纯词法 rank2：1/62≈0.0161）——加权单调性钉测。
    """
    lex = [_lex(1, 9.0, "a"), _lex(2, 8.0, "b")]
    vec = [_vec(3, 0.9, "c"), _vec(1, 0.8, "a")]
    low = fm.rrf_fuse(lex, vec, vec_weight=0.3, top_k=5)
    high = fm.rrf_fuse(lex, vec, vec_weight=5.0, top_k=5)
    assert [h["asset_id"] for h in low] == [1, 2, 3]
    assert [h["asset_id"] for h in high] == [1, 3, 2]


def test_rrf_rank_window_is_top10() -> None:
    """两路各取 top-10 排名：第 11 位起的块不参与（width 截断口径）。"""
    lex = [_lex(i, 10.0 - i) for i in range(1, 13)]  # 12 块词法
    vec = [_vec(1, 0.9, "c")]  # 块 1 与词法 rank1 同键（两路相加）
    hits = fm.rrf_fuse(lex, vec, vec_weight=0.5, top_k=12)
    assert len(hits) == 10  # 只有词法前 10 名进入排名域
    assert 11 not in [h["asset_id"] for h in hits] and 12 not in [h["asset_id"] for h in hits]


# ---------- C 加权线性：归一与并集 ----------


def test_linear_minmax_normalization_domain_is_full_lexical_hits() -> None:
    """归一域 = 词法命中全集：max->1.0、min->0.0、max==min（单块）全 1.0。"""
    lex = [_lex(1, 4.0, "a"), _lex(2, 2.0, "b"), _lex(3, 0.0, "c")]
    hits = fm.linear_fuse(lex, [], vec_weight=0.5, top_k=5)
    scores = {h["asset_id"]: h["score"] for h in hits}
    assert scores[1] == 1.0 and scores[2] == 0.5 and scores[3] == 0.0

    single = fm.linear_fuse([_lex(9, 0.3, "z")], [], vec_weight=0.5, top_k=5)
    assert single[0]["score"] == 1.0  # 唯一命中即最强词法证据


def test_linear_vector_only_hit_has_zero_lexical_term() -> None:
    """向量补召回块（词法零分）词法项为 0：final = w × cos。"""
    lex = [_lex(1, 4.0, "a")]
    vec = [_vec(7, 0.9, "v")]
    hits = fm.linear_fuse(lex, vec, vec_weight=0.5, top_k=5)
    by_id = {h["asset_id"]: h["score"] for h in hits}
    assert by_id[7] == 0.45  # 0.5 × 0.9
    assert by_id[1] == 1.0  # 归一满格、无向量（NULL 块路径）cos 项为 0


def test_linear_both_roads_hit_sums_terms() -> None:
    """两路都召回的块：归一词法分 + w × cos。"""
    lex = [_lex(1, 4.0, "a"), _lex(2, 0.0, "b")]
    vec = [_vec(1, 0.8, "a")]
    hits = fm.linear_fuse(lex, vec, vec_weight=0.5, top_k=5)
    by_id = {h["asset_id"]: h["score"] for h in hits}
    assert by_id[1] == 1.0 + 0.5 * 0.8
    assert by_id[2] == 0.0


# ---------- 亲和后置 ----------


def test_apply_affinity_after_reranks_within_window() -> None:
    """融合 top-10 窗内乘 1+3×aff 重排再截 top_k——低融合分高亲和块可上位。"""
    hits = [
        {**_lex(i, 10.0 - i), "score": 10.0 - i} for i in range(1, 11)
    ]  # 融合窗 10 块（1 最高）
    aff_of = {5: 1.0}  # 块 5 亲和满格：5 × (1+3) = 20 -> 压过块 1 的 10
    out = fm.apply_affinity_after(hits, aff_of, top_k=3)
    assert [h["asset_id"] for h in out] == [5, 1, 2]
    # 窗外（第 11 位）不参与重排
    long_hits = [*hits, {**_lex(99, 0.5), "score": 0.5}]
    out2 = fm.apply_affinity_after(long_hits, {99: 1.0}, top_k=11)
    assert 99 not in [h["asset_id"] for h in out2]


def test_apply_affinity_after_neutral_affinity_is_noop_order() -> None:
    """亲和全 0（中性面）= 乘数恒 1：名次不变只截 top_k。"""
    hits = [{**_lex(i, 10.0 - i), "score": 10.0 - i} for i in range(1, 6)]
    out = fm.apply_affinity_after(hits, {}, top_k=3)
    assert [h["asset_id"] for h in out] == [1, 2, 3]
