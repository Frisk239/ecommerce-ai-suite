"""collect_questions 纯函数离线单测（第 101 刀）：抽问去重/探针排除/预分类启发。

全部离线纯函数（normalize_question / is_probe_question / name_hits /
classify_candidate / merge_stats / build_candidates），不连 DB。脚本以
sys.path 直插方式导入（先例 test_rag_eval_tools）。
"""

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
sys.path.insert(0, str(EVAL_DIR))

import collect_questions as cq  # noqa: E402

# 预分类名集样本（演示库形态：资产标题 + subject 商品名 + 类目词的并集缩影）
NAMES = [
    "钛钢保温杯 · 规格",
    "钛钢保温杯",
    "退换货政策",
    "显示器商品图 · 实拍帧",
    "显示器商品图",
    "显示器",
    "耳机",
    "键盘",
    "Xperia Ear 规格（Wikidata）",
]


# ---------------------------------------------------------------- 归一/探针


def test_normalize_question_strips_whitespace_and_marks() -> None:
    assert cq.normalize_question(" 保温杯的净含量是多少？") == "保温杯的净含量是多少"
    assert cq.normalize_question("保温杯的净含量是多少??？") == "保温杯的净含量是多少"
    assert cq.normalize_question("怎么退货") == "怎么退货"
    assert cq.normalize_question("   ") == ""


def test_is_probe_question_matches_demo_probe_shapes() -> None:
    assert cq.is_probe_question("第0问：净含量？")
    assert cq.is_probe_question("第12问：净含量？")
    assert cq.is_probe_question("thermos capacity?")
    assert cq.is_probe_question("zxqw tiuu 838383")
    assert not cq.is_probe_question("保温杯的净含量是多少")
    assert not cq.is_probe_question("第0问完了吗")  # 只认「第N问：」前缀形态


# ---------------------------------------------------------------- 名字命中


def test_name_hits_prefers_long_and_dedupes_nested() -> None:
    # 长名优先：命中「钛钢保温杯」后「保温杯」（若在名集）不重复计——本名集无
    assert cq.name_hits("钛钢保温杯的净含量是多少", NAMES) == ["钛钢保温杯"]
    # 双类目命中：多商品问句的主形态
    assert set(cq.name_hits("显示器和耳机你们都有吗", NAMES)) == {"显示器", "耳机"}
    assert cq.name_hits("键盘有机械的吗", NAMES) == ["键盘"]
    assert cq.name_hits("怎么退货", NAMES) == []
    # 单字名不成判定（「书」「杯」太泛）
    assert cq.name_hits("书还有么", NAMES + ["书"]) == []


def test_classify_candidate_heuristics() -> None:
    # ≥2 名字 -> 混淆候选（优先级最高，即使引擎 answer 带引用）
    assert cq.classify_candidate(
        "显示器和耳机你们都有吗", agent_kind="answer", agent_cited=True, names=NAMES
    ) == ["confusion"]
    # answer 带引用 -> 正例/同义候选
    assert cq.classify_candidate(
        "怎么退货", agent_kind="answer", agent_cited=True, names=NAMES
    ) == ["positive", "paraphrase"]
    # answer 无引用（订单/库存/目录工具面）-> 不在大集
    assert (
        cq.classify_candidate("鼠标有货吗", agent_kind="answer", agent_cited=False, names=NAMES)
        == []
    )
    # refusal/handoff -> 拒答或表外同义候选
    assert cq.classify_candidate(
        "有赠品吗", agent_kind="refusal", agent_cited=False, names=NAMES
    ) == ["refusal", "oov_syn"]
    assert cq.classify_candidate(
        "我要转人工", agent_kind="handoff", agent_cited=False, names=NAMES
    ) == ["refusal", "oov_syn"]
    # kind 空（无 agent 回复）-> 不给建议
    assert cq.classify_candidate("还有吗", agent_kind=None, agent_cited=False, names=NAMES) == []


# ---------------------------------------------------------------- 合并/装配


def test_merge_stats_counts_forms_and_takes_latest_behavior() -> None:
    occs = [
        {"question": "保温杯的净含量是多少？", "kind": "answer", "cited": True},
        {"question": "保温杯的净含量是多少", "kind": "answer", "cited": True},
        {"question": "保温杯的净含量是多少？", "kind": "refusal", "cited": False},
    ]
    stats = cq.merge_stats(occs)
    assert stats["count"] == 3
    assert stats["question"] == "保温杯的净含量是多少？"  # 最高频原形
    assert stats["agent_kind"] == "refusal"  # 最近一次行为
    assert stats["agent_cited"] is False
    assert stats["agent_refused_ratio"] == 0.3333  # round 4 位（实现口径）


def test_merge_stats_rejects_mixed_keys() -> None:
    with pytest.raises(ValueError):
        cq.merge_stats(
            [
                {"question": "怎么退货", "kind": "answer", "cited": True},
                {"question": "怎么退款", "kind": "answer", "cited": True},
            ]
        )


def test_build_candidates_groups_by_normalized_key() -> None:
    occs = [
        {"question": "怎么退货？", "kind": "answer", "cited": True},
        {"question": "怎么退货", "kind": "answer", "cited": True},
        {"question": "显示器和耳机你们都有吗", "kind": "answer", "cited": True},
    ]
    candidates = cq.build_candidates(occs, NAMES)
    assert len(candidates) == 2
    by_q = {c["question"]: c for c in candidates}
    assert by_q["怎么退货？"]["count"] == 2
    assert by_q["怎么退货？"]["suggest"] == ["positive", "paraphrase"]
    assert by_q["显示器和耳机你们都有吗"]["suggest"] == ["confusion"]
    # 按计数降序、同计数按问句稳定排序
    assert candidates == sorted(candidates, key=lambda c: (-c["count"], c["question"]))
