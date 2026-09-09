"""评测尺离线单测（第 35 刀）：生成器纯函数 + runner 统计纯函数 + 同义词改写。

全部离线：assets 用 dict 行 fixture（generate_golden.asset_row 同形状），不连
DB、不调 LLM。scripts/eval 以 sys.path 直插方式导入（先例 test_realdata_scripts）。
"""

import json
import random
import sys
from pathlib import Path

from suite_api.services.retrieval import query_terms, score_chunk
from suite_api.services.synonyms import SYNONYM_GROUPS, apply_synonyms

EVAL_DIR = Path(__file__).resolve().parents[3] / "scripts" / "eval"
sys.path.insert(0, str(EVAL_DIR))

import generate_golden as gg  # noqa: E402
import run_eval as runner  # noqa: E402

SEED = 42

# 话题清单：前两条在 fixture 库里有证据（吊牌/退货），后两条无证据
_TOPICS = (
    "退货要保留吊牌吗",
    "吊牌丢了还能退货吗",
    "宠物用品在几楼卖",
    "机票改签找谁",
)


def _assets() -> list[dict]:
    return [
        gg.asset_row(1, 3, "钛钢保温杯 · 规格", "document", "upload", [
            "钛钢保温杯规格",
            "净含量：500ml",
            "材质：316不锈钢",
        ]),
        gg.asset_row(2, 1, "退货政策", "document", "upload", [
            "签收后7天内可申请退货",
            "商品需保持未使用状态且吊牌完整",
        ]),
        gg.asset_row(9, 1, "保温杯的净含量是多少？", "dialogue", "session_backflow", [
            "顾客：保温杯的净含量是多少？",
            "客服：净含量为500ml。",
            "问：保温杯的净含量是多少？\n答：净含量为 500ml。",
        ]),
        gg.asset_row(10, 1, "钛钢保温杯 500ml 316不锈钢", "material", "material_generated", [
            "钛钢保温杯，日常饮水随行",
            "材质：316不锈钢",
            "净含量：500ml",
        ]),
    ]


# ---------------------------------------------------------------- 同义词表/改写


def test_synonym_groups_shape() -> None:
    assert len(SYNONYM_GROUPS) >= 15, "spec Must 4：~15 组核心电商同义词"
    members = [m for g in SYNONYM_GROUPS for m in g]
    assert len(members) == len(set(members)), "同一词不得跨组重复（改写方向才有定义）"
    assert all(len(g) >= 2 for g in SYNONYM_GROUPS), "每组至少两个说法"


def test_apply_synonyms_rotates_within_group() -> None:
    group = next(g for g in SYNONYM_GROUPS if "保修" in g)
    for member in group:
        out = apply_synonyms(member)
        assert out in group and out != member, f"{member!r} 应轮转到同组另一说法"


def test_apply_synonyms_longest_match_first() -> None:
    # 折扣 是 折扣券 的前缀：3 字词必须整体轮转，不得被 2 字规则先吃掉
    assert apply_synonyms("折扣券") == "优惠券"
    # 保温瓶 是 3 字词，整体轮转（表内另一组的 2 字规则不影响它）
    assert apply_synonyms("保温瓶") == "保温杯"
    # 不在表里的词原样保留
    assert apply_synonyms("订单号SO-1001") == "订单号SO-1001"
    assert apply_synonyms("") == ""


def test_apply_synonyms_common_terms() -> None:
    assert apply_synonyms("净含量是多少") == "容量是多少"
    assert apply_synonyms("快递到哪了") == "物流到哪了"
    assert apply_synonyms("质保多久") == "三包多久"


# ---------------------------------------------------------------- 生成器：候选/改写


def test_field_pair_excludes_speakers_and_meta() -> None:
    assert gg.field_pair("净含量：500ml") == ("净含量", "500ml")
    assert gg.field_pair("顾客：保温杯的净含量是多少？") is None
    assert gg.field_pair("客服：净含量为500ml。") is None
    assert gg.field_pair("来源：https://world.example.org/x") is None  # 元数据脚注
    assert gg.field_pair("普通句子没有字段分隔") is None


def test_qa_pair_question_strips_punctuation() -> None:
    chunk = "问：保温杯的净含量是多少？\n答：净含量为 500ml。"
    assert gg.qa_pair_question(chunk) == "保温杯的净含量是多少"
    assert gg.qa_pair_question("顾客：不是问句") is None


def test_rephrase_changes_both_wording_and_pattern() -> None:
    # 保温杯->保温瓶（同义轮转）+ 是多少->有多少（句式变换），两处都变
    assert gg.rephrase("钛钢保温杯的保温时长是多少") == "钛钢保温瓶的保温时长有多少"
    assert gg.rephrase("钛钢保温杯的净含量是多少") == "钛钢保温瓶的容量有多少"
    # 退货->退换（同义轮转）+ 怎么样->请问…值得入手吗（句式变换）
    assert gg.rephrase("退货政策怎么样") == "请问退换政策值得入手吗"
    out = gg.rephrase("钛钢保温杯的净含量是500ml吗")
    assert out != "钛钢保温杯的净含量是500ml吗", "改写不得恒等"


def test_rephrase_idempotent_shape() -> None:
    # 一次改写后仍应是自然问句形态：以吗/多少/吧结尾或带前缀
    out = gg.rephrase("保温杯怎么样")
    assert out.endswith("值得入手吗")


# ---------------------------------------------------------------- 生成器：build_cases


def test_build_cases_shapes_and_expect() -> None:
    assets = _assets()
    pool = {(a["asset_id"], q) for a in assets for q in gg.positive_candidates(a)}
    cases = gg.build_cases(assets, _TOPICS, random.Random(SEED))

    assert cases, "小库也应产出用例"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case id 必须唯一"
    dists = {c["distribution"] for c in cases}
    assert dists <= {"positive", "paraphrase", "confusion", "refusal"}

    for case in cases:
        expect = case["expect"]
        if case["distribution"] in ("positive", "paraphrase"):
            assert set(expect) == {"cite"}
            assert set(expect["cite"]) == {"asset_id", "version_no"}
            # expect 正确性：问句必须能追溯到期望资产自身的候选（或其改写）
            origin = {
                (asset_id, q)
                for asset_id, q in pool
                if q == case["question"]
                or gg.rephrase(q) == case["question"]
            }
            assert (expect["cite"]["asset_id"], case["question"]) in origin or any(
                asset_id == expect["cite"]["asset_id"] for asset_id, _ in origin
            ), f"{case['id']} 的 expect 与问句来源不符"
        elif case["distribution"] == "refusal":
            assert expect == {"refuse": True}
            assert case["question"] in _TOPICS
        else:
            assert set(expect["cite"]) == {"asset_id", "version_no"}


def test_build_cases_refusal_topics_probed_by_lexicon() -> None:
    assets = _assets()
    cases = gg.build_cases(assets, _TOPICS, random.Random(SEED))
    refused = [c["question"] for c in cases if c["distribution"] == "refusal"]
    # 前两条（吊牌/退货在 fixture 块里有词法证据）必须被剔除
    assert "退货要保留吊牌吗" not in refused
    assert "吊牌丢了还能退货吗" not in refused
    assert set(refused) == {"宠物用品在几楼卖", "机票改签找谁"}
    assert gg.surviving_refusal_topics(assets, _TOPICS) == [
        "宠物用品在几楼卖",
        "机票改签找谁",
    ]


def test_build_cases_seed_reproducible() -> None:
    assets = _assets()
    a = gg.build_cases(assets, _TOPICS, random.Random(SEED))
    b = gg.build_cases(assets, _TOPICS, random.Random(SEED))
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)


def test_build_confusions_cjk_only_and_expect_by_score() -> None:
    assets = _assets()
    confusions = gg.build_confusions(assets, 100)
    assert confusions, "fixture 资产间存在共有中文 bigram"
    for conf in confusions:
        word = conf["word"]
        assert all("\u4e00" <= ch <= "\u9fff" for ch in word), "英文碎片不得成混淆词"
        # expect 资产与词法分一致：分高者胜，同分取 asset_id 小
        left, right = assets[0], assets[1]
        sl = gg.confusion_word_score(word, left)
        sr = gg.confusion_word_score(word, right)
        want = left if sl >= sr else right if sr > sl else min(left, right, key=lambda a: a["asset_id"])
        if conf["expect_asset"] in (left, right):
            assert conf["expect_asset"] is want or conf["expect_asset"]["asset_id"] == want["asset_id"]
        # 词必须真的同时出现在某两资产
        hits = sum(1 for a in assets if word in gg._asset_bigrams(a))
        assert hits >= 2
    assert all(conf["word"] not in {"It", "df", "wi"} for conf in confusions)


def test_confusion_question_terms_match_retrieval() -> None:
    # 「{词}怎么样」的有效词法单元恰为词本身（怎么样全停用）——与 runner 打分同口径
    assets = _assets()
    confusions = gg.build_confusions(assets, 1)
    word = confusions[0]["word"]
    assert query_terms(f"{word}怎么样") == query_terms(word)
    best = max(score_chunk(query_terms(word), c) for a in assets for c in a["chunks"])
    assert best > 0


# ---------------------------------------------------------------- runner：统计纯函数


def _case(dist: str, expect: dict) -> dict:
    return {"id": f"{dist}-x", "distribution": dist, "question": "q", "expect": expect}


def test_judge_case_recall_and_refusal() -> None:
    cite = _case("positive", {"cite": {"asset_id": 1, "version_no": 2}})
    row = runner.judge_case(cite, [{"asset_id": 1, "version_no": 2, "chunk": "c", "score": 1.0}], "answer")
    assert row["recall1"] is True and row["recall3"] is True and row["refused"] is False

    wrong_version = runner.judge_case(
        cite, [{"asset_id": 1, "version_no": 1, "chunk": "c", "score": 1.0}], "answer"
    )
    assert wrong_version["recall1"] is False and wrong_version["recall3"] is True

    miss = runner.judge_case(cite, [], "refusal")
    assert miss["recall1"] is False and miss["recall3"] is False and miss["refused"] is True

    ref = runner.judge_case(_case("refusal", {"refuse": True}), [], "refusal")
    assert ref["refused"] is True and ref["recall1"] is None

    conf = _case("confusion", {"cite": {"asset_id": 7, "version_no": 1}})
    crow = runner.judge_case(
        conf, [{"asset_id": 7, "version_no": 1, "chunk": "c", "score": 1.0}], "answer"
    )
    assert crow["confusion_top1"] is True


def test_aggregate_hand_computed() -> None:
    hit = {"asset_id": 1, "version_no": 1, "chunk": "c", "score": 1.0}
    rows = [
        runner.judge_case(_case("positive", {"cite": {"asset_id": 1, "version_no": 1}}), [hit], "answer"),
        runner.judge_case(_case("positive", {"cite": {"asset_id": 2, "version_no": 1}}), [], "refusal"),
        runner.judge_case(_case("paraphrase", {"cite": {"asset_id": 3, "version_no": 1}}), [hit], "answer"),
        runner.judge_case(_case("confusion", {"cite": {"asset_id": 9, "version_no": 1}}), [hit], "answer"),
        runner.judge_case(_case("refusal", {"refuse": True}), [], "refusal"),
    ]
    rows[0]["faithful"] = True
    rows[2]["faithful"] = False
    agg = runner.aggregate(rows)
    assert agg["positive"]["n"] == 2
    assert agg["positive"]["recall1"] == 0.5
    assert agg["positive"]["false_refusal"] == 0.5
    assert agg["paraphrase"]["recall1"] == 0.0
    assert agg["confusion"]["confusion_top1"] == 0.0
    assert agg["refusal"]["refusal_rate"] == 1.0
    assert agg["overall"]["n"] == 5
    assert agg["overall"]["recall1"] == 0.25
    assert agg["overall"]["judged"] == 2
    assert agg["overall"]["faithful"] == 0.5


def test_format_table_na_and_judge_column() -> None:
    rows = [
        runner.judge_case(_case("refusal", {"refuse": True}), [], "refusal"),
    ]
    table = runner.format_table(runner.aggregate(rows), judge_on=False)
    assert "refusal" in table and "100.0%" in table
    assert "忠实度" not in table
    table_j = runner.format_table(runner.aggregate(rows), judge_on=True)
    assert "忠实度" in table_j


def test_supported_verdict_parsing() -> None:
    assert runner._supported_verdict('{"supported": true}') is True
    assert runner._supported_verdict('{"supported": false}') is False
    assert runner._supported_verdict('{"supported": "false"}') is False
    assert runner._supported_verdict("回答没有证据，false") is False
    assert runner._supported_verdict("完全无法解析的输出") is None


def test_build_judge_prompt_contains_parts() -> None:
    prompt = runner.build_judge_prompt("Q", ["证据一", "证据二"], "回答A")
    assert "Q" in prompt and "证据一" in prompt and "证据二" in prompt and "回答A" in prompt
