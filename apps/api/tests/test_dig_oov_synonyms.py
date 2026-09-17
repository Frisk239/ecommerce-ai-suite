"""dig_oov_synonyms 纯函数离线单测（第 104 刀）：分词假设/频次分拣/清单形状。

全部离线（candidate_spans / span_oov_state / hypothesis_question /
triage_label / build_candidate / rank_targets / corpus_vocabulary /
hypothesis_rules），不连 DB。脚本以 sys.path 直插方式导入（先例
test_collect_questions）。--ab 主流程连库，不入本文件（与 collect_questions
同口径：主流程由真库评测工件核验）。
"""

import sys
from pathlib import Path

import pytest

from suite_api.services import synonyms
from suite_api.services.retrieval import query_terms
from suite_api.services.synonyms import apply_synonyms

EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
sys.path.insert(0, str(EVAL_DIR))

import dig_oov_synonyms as dig  # noqa: E402

# ---------------------------------------------------------------- 分词（candidate_spans）


def test_spans_split_on_stop_and_non_chinese() -> None:
    """停用字/非中文字符切段：坏了给修吗 -> 只剩「给修」（坏/了/吗 是停用或孤立单字）。"""
    assert dig.candidate_spans("坏了给修吗") == ("给修",)


def test_spans_sliding_windows_longest_first() -> None:
    """段内 2-4 字滑窗（上限 SPAN_MAX_CHARS=4）、长词在前、去重保序。"""
    # 能/在/吗 停字（上不是）-> 单段「键盘上雕字」（5 字），滑窗截到 4 字
    assert dig.candidate_spans("可以在键盘上雕字吗") == (
        "键盘上雕",
        "盘上雕字",
        "键盘上",
        "盘上雕",
        "上雕字",
        "键盘",
        "盘上",
        "上雕",
        "雕字",
    )


def test_spans_english_and_digits_are_cut() -> None:
    """英文型号/数字不成候选（非中文即断）。"""
    assert dig.candidate_spans("Xperia Ear Duo 哪一年出的") == ("一年出", "一年", "年出")


def test_spans_empty_and_single_char() -> None:
    assert dig.candidate_spans("") == ()
    assert dig.candidate_spans("的吗呢") == ()
    assert dig.candidate_spans("坏") == ()  # 单字不成候选


# ---------------------------------------------------------------- 缺口状态（span_oov_state）


def test_oov_state_full_partial_in() -> None:
    """full=全部 bigram 缺、partial=部分缺、in=全在。"""
    corpus = frozenset({"保温", "运费", "邮费"})
    assert dig.span_oov_state("邮资", corpus) == "full"  # 邮资 的 bigram 全缺
    assert dig.span_oov_state("保温壶", corpus) == "partial"  # 保温 在、温壶 缺
    assert dig.span_oov_state("运费", corpus) == "in"
    assert dig.span_oov_state("的吗", corpus) == "in"  # 无有效 bigram -> 不算缺口


# ---------------------------------------------------------------- 假设问句（hypothesis_question）


def test_hypothesis_question_replaces_first_occurrence_only() -> None:
    assert dig.hypothesis_question("坏了给修吗", "给修", "保修") == "坏了保修吗"
    assert dig.hypothesis_question("退掉再退掉", "退掉", "退货") == "退货再退掉"


# ---------------------------------------------------------------- 频次分拣（triage_label）


def test_triage_priority_on_freq_or_probe() -> None:
    """频次 >=2 或出现在探针里 -> priority；频次 1 且非探针 -> deferred。"""
    assert dig.triage_label(2, False) == "priority"
    assert dig.triage_label(0, True) == "priority"
    assert dig.triage_label(1, False) == "deferred"


# ---------------------------------------------------------------- 清单形状（build_candidate）


def test_build_candidate_shape() -> None:
    """候选清单行形状：token/频次/探针/来源/缺口状态/分拣/假设列表，来源截断。"""
    cand = dig.build_candidate(
        "雕字",
        0,
        ["exp-oovsyn-004"],
        [f"问句{i}" for i in range(8)],
        "full",
        [{"target": "刻字", "group": None, "hyp_r1": True}],
    )
    assert cand["token"] == "雕字"
    assert cand["freq"] == 0
    assert cand["in_probe"] == ["exp-oovsyn-004"]
    assert cand["sources"] == [f"问句{i}" for i in range(dig.MAX_SOURCES)]
    assert cand["oov_state"] == "full"
    assert cand["triage"] == "priority"  # 探针词即 priority
    assert cand["hypotheses"][0]["target"] == "刻字"
    assert dig.build_candidate("孤词", 1, [], [], "full", [])["triage"] == "deferred"


# ---------------------------------------------------------------- 本地预筛（rank_targets）


def _fake_index(chunks: list[str]) -> tuple[list[frozenset[str]], dict[str, tuple[int, ...]]]:
    terms_seq = [query_terms(c) for c in chunks]
    inverted: dict[str, list[int]] = {}
    for cid, terms in enumerate(terms_seq):
        for term in terms:
            inverted.setdefault(term, []).append(cid)
    return terms_seq, {k: tuple(v) for k, v in inverted.items()}


def test_rank_targets_scores_anchor_hits_first() -> None:
    """能撞上块的目标词进榜（分>0）、与锚无关的词不进榜；降序返回。"""
    terms_seq, inverted = _fake_index(["整机保修一年", "质量问题退换运费由商家承担"])
    ranked = dig.rank_targets("东东坏了吗", "东东", ["保修", "运费", "净含量"], terms_seq, inverted)
    targets = [t for t, _s in ranked]
    assert "保修" in targets  # 块 1 含 保修 bigram
    assert "运费" in targets  # 块 2 含 运费 bigram
    assert "净含量" not in targets  # 两块都不含 -> gain 0 不进榜
    scores = [s for _t, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_targets_skips_target_already_in_question() -> None:
    """目标词已在问句里 -> 替换无新 bigram -> 不进榜。"""
    terms_seq, inverted = _fake_index(["运费由商家承担"])
    assert dig.rank_targets("运费怎么算", "怎么算", ["运费"], terms_seq, inverted) == []


def test_rank_targets_only_chunk_ids_restricts_scoring() -> None:
    """only_chunk_ids：只在给定（期望资产）的块上计分——探针按锚排序的口径，
    短块的无关高分词被滤掉。"""
    terms_seq, inverted = _fake_index(["容量：500ml", "质量问题退换运费由商家承担"])
    all_ranked = dig.rank_targets("东东坏了吗", "东东", ["容量", "运费"], terms_seq, inverted)
    anchor_ranked = dig.rank_targets(
        "东东坏了吗",
        "东东",
        ["容量", "运费"],
        terms_seq,
        inverted,
        only_chunk_ids=frozenset({1}),
    )
    assert [t for t, _s in all_ranked] == ["容量", "运费"]  # 短块（容量：500ml）绝对分更高
    assert [t for t, _s in anchor_ranked] == ["运费"]  # 锚块 1 上只有 运费 有分


# ---------------------------------------------------------------- 语料词表（corpus_vocabulary）


def test_corpus_vocabulary_df_counts_texts() -> None:
    """中文连续段 2-4 字滑窗 -> 文档频（出现文本数）。"""
    df = dig.corpus_vocabulary(["整机保修一年", "保修政策说保修两年"])
    assert df["整机"] == 1
    assert df["保修"] == 2
    assert df["整机保修"] == 1


# ---------------------------------------------------------------- 试探边（hypothesis_rules）


def test_hypothesis_rules_emulates_collection_and_restores() -> None:
    """试探边生效（表外词轮转到目标词）且退出恢复原表；既有轮转（环组+对组）
    不受影响（用不在表内的试探词，避免与 104 刀真收词撞形）。"""
    original = synonyms._SYNONYM_RULES
    with dig.hypothesis_rules("东东", "保修"):
        assert apply_synonyms("东东坏了吗") == "保修坏了吗"
        assert apply_synonyms("保修政策") == "质保政策"  # 环组既有轮转不变
        assert apply_synonyms("雕字服务") == "刻字服务"  # 对组既有轮转不变
    assert synonyms._SYNONYM_RULES == original
    assert apply_synonyms("东东坏了吗") == "东东坏了吗"  # 恢复后试探词不再轮转


def test_hypothesis_rules_survives_exception() -> None:
    """块内抛异常也必须恢复原表（挖掘主流程逐假设 try 外的安全网）。"""
    original = synonyms._SYNONYM_RULES
    with pytest.raises(RuntimeError):
        with dig.hypothesis_rules("试探词", "刻字"):
            raise RuntimeError("boom")
    assert synonyms._SYNONYM_RULES == original
