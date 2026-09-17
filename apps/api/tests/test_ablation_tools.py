"""第 107b 刀三级消融纯函数离线单测（LevelConfig 注入形状 / 词法级 / 亲和乘数 /
三级组装 / 增量归因）——脚本内函数导入测试（先例 test_fusion_matrix_tools：
sys.path 直插 scripts/eval），不连 DB、不调云。

钉的是 scripts/eval/ablation.py 的**消融点语义**：L1 裸词法不受亲和影响、
L2 乘数重排、L3 走现役 fuse_dense_sparse（vec=None 时与 L2 同序、词法空手
三级全空手=lexgate）；L3 与生产 retrieve 的逐位等值由脚本 verify_production
在真库复核（不在此测）。
"""

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
sys.path.insert(0, str(EVAL_DIR))

import ablation as ab  # noqa: E402


def _hit(asset_id: int, score: float, chunk: str = "c") -> dict:
    return {"asset_id": asset_id, "version_no": 1, "chunk": chunk, "score": score}


def _vec(asset_id: int, cos: float, chunk: str = "c") -> dict:
    return {"asset_id": asset_id, "version_no": 1, "chunk": chunk, "cos": cos}


# ---------- LevelConfig：三级阶梯定义 ----------


def test_level_configs_are_monotone_single_feature_ladder() -> None:
    """三级=每级恰加一个特征：L1 裸词法、L2 = L1+亲和、L3 = L2+融合。"""
    l1, l2, l3 = ab.LEVELS
    assert (l1.affinity, l1.fusion) == (False, False)
    assert (l2.affinity, l2.fusion) == (True, False)
    assert (l3.affinity, l3.fusion) == (True, True)


# ---------- level_hits：三级配置注入 ----------


def test_level_hits_l1_ignores_affinity_and_keeps_bare_order() -> None:
    """L1：亲和映射非空也不乘——裸词法分原序（消融点：亲和只在 L2 起）。"""
    bare = [_hit(1, 2.0), _hit(2, 1.0)]
    aff_of = {2: 1.0}  # 资产 2 亲和满格，但 L1 不看它
    hits = ab.level_hits(ab.LevelConfig("L1"), bare, aff_of, top_k=3)
    assert [h["asset_id"] for h in hits] == [1, 2]
    assert hits[1]["score"] == 1.0  # 分数原样


def test_level_hits_l2_affinity_rerank_promotes_named_asset() -> None:
    """L2：低裸分高点名资产乘 1+3×aff 上位（79 刀跨商品混淆破并列的形态）。"""
    bare = [_hit(1, 2.0), _hit(2, 0.6)]
    aff_of = {2: 1.0}  # 0.6 × (1+3) = 2.4 > 2.0
    hits = ab.level_hits(ab.LevelConfig("L2", affinity=True), bare, aff_of, top_k=3)
    assert [h["asset_id"] for h in hits] == [2, 1]
    assert hits[0]["score"] == 2.4


def test_level_hits_l2_neutral_affinity_is_noop_order() -> None:
    """L2 中性面：问句与标题无交集（aff 全 0/缺省）时乘数恒 1，名次不变。"""
    bare = [_hit(1, 2.0), _hit(2, 1.0)]
    hits = ab.level_hits(ab.LevelConfig("L2", affinity=True), bare, {}, top_k=3)
    assert [h["asset_id"] for h in hits] == [1, 2]
    assert [h["score"] for h in hits] == [2.0, 1.0]


def test_level_hits_l3_without_vectors_equals_l2_order() -> None:
    """L3 无向量路（vec=None：未配 key/失败的生产退化形态）：min-max 归一单调，
    名次与 L2 逐位一致（含并列键）。"""
    bare = [_hit(1, 2.0), _hit(2, 1.0), _hit(3, 1.0)]
    aff_of = {2: 0.5}
    cfg2 = ab.LevelConfig("L2", affinity=True)
    cfg3 = ab.LevelConfig("L3", affinity=True, fusion=True)
    l2 = ab.level_hits(cfg2, bare, aff_of, None, top_k=3)
    l3 = ab.level_hits(cfg3, bare, aff_of, None, top_k=3)
    assert [h["asset_id"] for h in l3] == [h["asset_id"] for h in l2]


def test_level_hits_l3_vector_only_recall_enters_between() -> None:
    """L3 向量补召回：不在词法命中集的块词法项 0、final = 0.5×cos，可插入榜中。"""
    bare = [_hit(1, 4.0, "a"), _hit(2, 0.0, "b")]  # 归一后 1.0 / 0.0
    vec = [_vec(7, 0.9, "v")]  # 补召回块：0.5×0.9=0.45，介于两者之间
    hits = ab.level_hits(ab.LevelConfig("L3", affinity=True, fusion=True), bare, {}, vec, top_k=3)
    assert [h["asset_id"] for h in hits] == [1, 7, 2]
    assert hits[1]["score"] == 0.45


def test_level_hits_empty_lexical_all_levels_empty_lexgate() -> None:
    """词法空手闸：bare 空 + 向量近邻在场也空手出——三级拒答语义全由词法路决定。"""
    vec = [_vec(7, 0.95)]
    for cfg in ab.LEVELS:
        assert ab.level_hits(cfg, [], {}, vec, top_k=3) == []


def test_level_hits_l3_uses_affinity_before_fusion() -> None:
    """L3 亲和在融合前原位（affinity_before=现役生产形态）：乘过亲和的分进归一域。"""
    bare = [_hit(1, 2.0), _hit(2, 0.6)]
    aff_of = {2: 1.0}  # 亲和把 2 抬到 2.4 -> 归一满格 1.0，压过资产 1 的 0.0
    hits = ab.level_hits(
        ab.LevelConfig("L3", affinity=True, fusion=True), bare, aff_of, None, top_k=3
    )
    assert [h["asset_id"] for h in hits] == [2, 1]
    assert hits[0]["score"] == 1.0


def test_stable_sorted_ties_break_by_asset_then_chunk() -> None:
    """并列按 (asset_id, chunk) 稳定——与 retrieve 排序键同款（可复现）。"""
    hits = [_hit(2, 1.0, "b"), _hit(1, 1.0, "z"), _hit(1, 1.0, "a")]
    assert [ab._key(h)[0] for h in ab._stable_sorted(hits)] == [1, 1, 2]
    assert [h["chunk"] for h in ab._stable_sorted(hits)][0] == "a"


# ---------- lexical_stage：词法级（同义并集/评论闸/去重/stale） ----------


def _candidate(asset_id: int, chunk: str, *, source: str = "document", stale: float = 1.0) -> dict:
    return {
        "asset_id": asset_id,
        "version_no": 1,
        "chunk": chunk,
        "stale_mult": stale,
        "source_kind": source,
        "title": f"资产{asset_id}",
    }


def test_lexical_stage_scores_dedups_and_maps_affinity() -> None:
    """基本形状：命中打分、(asset,version,chunk) 去重保首、亲和映射按标题算。"""
    candidates = [
        _candidate(1, "净含量：550毫升"),
        _candidate(1, "净含量：550毫升"),  # 同键重复（合法双路证据）只留首条
        _candidate(2, "无关内容"),
    ]
    titles_of = {1: "钛钢保温杯", 2: "其他"}
    idf = ab.title_idf(list(titles_of.values()))
    bare, aff_of = ab.lexical_stage(candidates, titles_of, idf, "净含量是多少")
    assert [h["asset_id"] for h in bare] == [1]  # 无关块零命中、重复键去重
    assert set(aff_of) == {1, 2}  # 亲和映射覆盖全部候选资产（中性=0）


def test_lexical_stage_review_gate_drops_service_state_questions() -> None:
    """评论适用域闸：服务状态问（无观点标记）下 review_import 块不进候选；
    观点问（怎么样）放行——与 retrieve 同闸同口径。"""
    review = _candidate(9, "到货后看着很多", source="review_import")
    doc = _candidate(1, "到货时效说明")
    titles_of = {9: "洗发水", 1: "物流说明"}
    idf = ab.title_idf(list(titles_of.values()))
    service_bare, _ = ab.lexical_stage([review, doc], titles_of, idf, "到货了吗")
    assert [h["asset_id"] for h in service_bare] == [1]  # 评论块被闸掉
    opinion_bare, _ = ab.lexical_stage([review, doc], titles_of, idf, "到货快吗")
    assert 9 in [h["asset_id"] for h in opinion_bare]  # 观点问豁免（快吗）


def test_lexical_stage_applies_stale_multiplier() -> None:
    """stale 乘数在候选行注入（load_candidates 口径），词法级原样相乘。"""
    fresh = _candidate(1, "净含量：550毫升", stale=1.0)
    stale = _candidate(2, "净含量：330毫升", stale=0.5)
    titles_of = {1: "a", 2: "b"}
    idf = ab.title_idf(list(titles_of.values()))
    bare, _ = ab.lexical_stage([fresh, stale], titles_of, idf, "净含量是多少")
    by_id = {h["asset_id"]: h["score"] for h in bare}
    assert by_id[2] == by_id[1] * 0.5  # 同文块数下 stale 减半（含 sqrt 归一差异由分数自证）


# ---------- level_diff：增量归因 ----------


def _row(case_id: str, **metrics: object) -> dict:
    row = {
        "id": case_id,
        "distribution": "positive",
        "refused": False,
        "answered": True,
        "hit_asset_ids": [],
        "recall1": None,
        "recall3": None,
        "mrr": None,
        "ndcg3": None,
        "noise3": None,
        "confusion_top1": None,
        "faithful": None,
    }
    row.update(metrics)
    return row


def test_level_diff_boolean_and_float_metrics() -> None:
    """布尔指标：True 起来=gained、掉下去=lost；None 不计。"""
    before = [_row("a", recall1=False), _row("b", recall1=True), _row("c", recall1=None)]
    after = [_row("a", recall1=True), _row("b", recall1=False), _row("c", recall1=True)]
    gained, lost = ab.level_diff(before, after, key="recall1")
    assert gained == ["a"] and lost == ["b"]  # c 两级皆 None 不参与


def test_level_diff_noise3_inverts_truth_table() -> None:
    """噪声率越小越好：下降=gained（翻转真值表）。"""
    before = [_row("a", noise3=1.0), _row("b", noise3=0.33)]
    after = [_row("a", noise3=0.67), _row("b", noise3=0.67)]
    gained, lost = ab.level_diff(before, after, key="noise3")
    assert gained == ["a"] and lost == ["b"]


def test_level_diff_refused_tracks_refusal_flips() -> None:
    """refused 键：拒答翻转逐条对照（L3 融合不得改动拒答语义的测试面）。"""
    before = [_row("r1", refused=True), _row("r2", refused=False)]
    after = [_row("r1", refused=True), _row("r2", refused=True)]
    gained, lost = ab.level_diff(before, after, key="refused")
    assert gained == ["r2"] and lost == []


def test_redline_floor_semantics() -> None:
    """红线检查：positive@1 ≥ 79/80 且拒答率 ≥ 26/30 才 OK（97.5/83.3 双破）。"""
    ok = {"positive": {"recall1": 79 / 80}, "refusal": {"refusal_rate": 26 / 30}}
    assert ab.redline(ok) == (True, "OK")
    broken_pos = {"positive": {"recall1": 78 / 80}, "refusal": {"refusal_rate": 1.0}}
    broken_ref = {"positive": {"recall1": 1.0}, "refusal": {"refusal_rate": 25 / 30}}
    assert ab.redline(broken_pos)[0] is False
    assert ab.redline(broken_ref)[0] is False
