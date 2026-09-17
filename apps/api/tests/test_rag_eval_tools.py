"""评测尺离线单测（第 35 刀）：生成器纯函数 + runner 统计纯函数 + 同义词改写。

全部离线：assets 用 dict 行 fixture（generate_golden.asset_row 同形状），不连
DB、不调 LLM。scripts/eval 以 sys.path 直插方式导入（先例 test_realdata_scripts）。
"""

import json
import random
import sys
from pathlib import Path

import pytest

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


def test_distributions_include_oov_syn() -> None:
    """第 101 刀：第五分布（表外同义探针）进统计词表与表格。"""
    assert "oov_syn" in runner.DISTRIBUTIONS
    hit = {"asset_id": 1, "version_no": 1, "chunk": "c", "score": 1.0}
    rows = [
        runner.judge_case(_case("oov_syn", {"cite": {"asset_id": 1, "version_no": 1}}), [hit], "answer"),
        runner.judge_case(_case("oov_syn", {"cite": {"asset_id": 2, "version_no": 1}}), [], "refusal"),
    ]
    agg = runner.aggregate(rows)
    assert agg["oov_syn"]["n"] == 2
    assert agg["oov_syn"]["recall1"] == 0.5
    assert agg["oov_syn"]["confusion_top1"] is None  # 混淆@1 只对 confusion 组
    table = runner.format_table(agg, judge_on=False)
    assert "oov_syn" in table


def test_distributions_include_sem_neg() -> None:
    """第 107a 刀：第六分布（语义负例）进统计词表与表格，走 cite 组管线。"""
    assert "sem_neg" in runner.DISTRIBUTIONS
    hit = {"asset_id": 5, "version_no": 1, "chunk": "c", "score": 1.0}
    row = runner.judge_case(
        _case("sem_neg", {"cite": {"asset_id": 5, "version_no": 1}}), [hit], "answer"
    )
    assert row["recall1"] is True and row["recall3"] is True
    assert row["mrr"] == 1.0 and row["ndcg3"] == 1.0 and row["noise3"] == pytest.approx(2 / 3)
    agg = runner.aggregate([row])
    assert agg["sem_neg"]["n"] == 1
    assert agg["sem_neg"]["mrr"] == 1.0
    table = runner.format_table(agg, judge_on=False)
    assert "sem_neg" in table


# ---------------------------------------------------------------- 第 107a 刀：排序三指标纯函数


def _hit(asset_id: int, version_no: int = 1) -> dict:
    return {"asset_id": asset_id, "version_no": version_no, "chunk": "c", "score": 1.0}


def test_reciprocal_rank_position_boundaries() -> None:
    """MRR 边界：top1=1.0/top2=0.5/top3≈0.333/不中=0；期望多块取首块位次。"""
    assert runner.reciprocal_rank([_hit(1)], 1) == 1.0
    assert runner.reciprocal_rank([_hit(2), _hit(1)], 1) == 0.5
    assert runner.reciprocal_rank([_hit(2), _hit(3), _hit(1)], 1) == pytest.approx(1 / 3)
    # 不中（含零命中）：0
    assert runner.reciprocal_rank([_hit(2), _hit(3), _hit(4)], 1) == 0.0
    assert runner.reciprocal_rank([], 1) == 0.0
    # 期望多块进榜：MRR 只看首次出现位次（1/2，不叠加）
    assert runner.reciprocal_rank([_hit(2), _hit(1), _hit(1)], 1) == 0.5
    # 版本不同不影响（资产级口径，与 recall@3 一致）
    assert runner.reciprocal_rank([_hit(1, version_no=3)], 1) == 1.0


def test_discounted_gain_matches_p1_p2_p3() -> None:
    """nDCG@3 边界：p1=1.0/p2≈0.6309/p3=0.5（log2(i+1) 折扣）/不中=0；多块取首块。"""
    assert runner.discounted_gain([_hit(1)], 1) == 1.0
    # 1/log2(3) = 0.6309（spec 口径 p2=0.63）
    assert runner.discounted_gain([_hit(2), _hit(1)], 1) == pytest.approx(0.6309, abs=1e-4)
    # 1/log2(4) = 0.5（spec 口径 p3=0.5）
    assert runner.discounted_gain([_hit(2), _hit(3), _hit(1)], 1) == 0.5
    assert runner.discounted_gain([_hit(2), _hit(3), _hit(4)], 1) == 0.0
    assert runner.discounted_gain([], 1) == 0.0
    # 期望多块：只记首块位次折扣（取值域不超 1）
    assert runner.discounted_gain([_hit(1), _hit(1), _hit(1)], 1) == 1.0


def test_noise_rate_counts_expected_blocks() -> None:
    """噪声率@3 边界：(3-期望块数)/3；全部期望=0、不中=100%；短列表空位按非相关计。"""
    assert runner.noise_rate([_hit(1), _hit(1), _hit(1)], 1) == 0.0
    assert runner.noise_rate([_hit(1), _hit(2), _hit(1)], 1) == pytest.approx(1 / 3)
    assert runner.noise_rate([_hit(1), _hit(2), _hit(3)], 1) == pytest.approx(2 / 3)
    # 期望不在 top3（含零命中）：100%
    assert runner.noise_rate([_hit(2), _hit(3), _hit(4)], 1) == 1.0
    assert runner.noise_rate([], 1) == 1.0
    # 命中列表短于 3：空位按非相关计（分母恒 3——与 spec 公式逐字一致）
    assert runner.noise_rate([_hit(1)], 1) == pytest.approx(2 / 3)
    assert runner.noise_rate([_hit(1), _hit(1)], 1) == pytest.approx(1 / 3)


def test_metric_columns_flow_through_aggregate_and_table() -> None:
    """三指标列随 cite 期望落列、refusal 组不适用（None → 表显 -），聚合均值正确。"""
    cite = {"cite": {"asset_id": 1, "version_no": 1}}
    miss = {"cite": {"asset_id": 9, "version_no": 1}}
    rows = [
        runner.judge_case(_case("positive", cite), [_hit(1), _hit(2), _hit(3)], "answer"),
        runner.judge_case(_case("positive", miss), [_hit(4), _hit(5), _hit(6)], "answer"),
        runner.judge_case(_case("refusal", {"refuse": True}), [], "refusal"),
    ]
    agg = runner.aggregate(rows)
    assert agg["positive"]["mrr"] == 0.5  # (1.0 + 0.0) / 2
    assert agg["positive"]["ndcg3"] == 0.5  # (1.0 + 0.0) / 2
    assert agg["positive"]["noise3"] == round((2 / 3 + 1.0) / 2, 4)
    assert agg["refusal"]["mrr"] is None and agg["refusal"]["ndcg3"] is None
    assert agg["refusal"]["noise3"] is None
    table = runner.format_table(agg, judge_on=False)
    assert "MRR" in table and "nDCG@3" in table and "噪声@3" in table
    # refusal 行三列显示 -
    refusal_line = next(ln for ln in table.splitlines() if ln.startswith("refusal"))
    assert refusal_line.count("-") >= 3


def test_aggregate_cite_groups_share_recall_columns() -> None:
    """positive/paraphrase/confusion/oov_syn/sem_neg 五个 cite 组统一走 recall 判定。"""
    hit = {"asset_id": 5, "version_no": 2, "chunk": "c", "score": 1.0}
    for dist in ("positive", "paraphrase", "confusion", "oov_syn", "sem_neg"):
        row = runner.judge_case(_case(dist, {"cite": {"asset_id": 5, "version_no": 2}}), [hit], "answer")
        assert row["recall1"] is True and row["recall3"] is True


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


def test_judge_with_retry_recovers_within_attempts(monkeypatch) -> None:
    """第 87 刀韧性：前两次 LLMError、第三次成功 -> 评上（旧实现直接终止整列）。"""
    from suite_api.services.llm import LLMUnavailable

    calls: list[int] = []

    def fake_judge_one(question: str, chunks, answer: str) -> bool:
        calls.append(1)
        if len(calls) < runner.JUDGE_ATTEMPTS:
            raise LLMUnavailable("网关抖动")
        return True

    monkeypatch.setattr(runner, "judge_one", fake_judge_one)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    assert runner.judge_with_retry("Q", ["证据"], "回答") is True
    assert len(calls) == runner.JUDGE_ATTEMPTS


def test_judge_with_retry_fail_soft_returns_none(monkeypatch) -> None:
    """重试尽仍失败 -> None（fail-soft：LLMError 不逃逸，调用方记未评上继续跑）。"""
    from suite_api.services.llm import LLMUnavailable

    calls: list[int] = []

    def fake_judge_one(question: str, chunks, answer: str) -> bool:
        calls.append(1)
        raise LLMUnavailable("网关持续不可用")

    monkeypatch.setattr(runner, "judge_one", fake_judge_one)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    assert runner.judge_with_retry("Q", ["证据"], "回答") is None
    assert len(calls) == runner.JUDGE_ATTEMPTS


def test_judge_with_retry_no_sleep_on_last_attempt(monkeypatch) -> None:
    """最后一次失败后不再等待（attempts 次 LLMError 只睡 attempts-1 次）。"""
    from suite_api.services.llm import LLMError

    sleeps: list[float] = []

    def fake_judge_one(question: str, chunks, answer: str) -> bool:
        raise LLMError("输出不可解析")

    monkeypatch.setattr(runner, "judge_one", fake_judge_one)
    monkeypatch.setattr(runner.time, "sleep", lambda s: sleeps.append(s))
    assert runner.judge_with_retry("Q", ["证据"], "回答") is None
    assert sleeps == [runner.JUDGE_RETRY_WAIT_SECONDS] * (runner.JUDGE_ATTEMPTS - 1)


# ---------------------------------------------------------------- golden schema 自检（第 101 刀）


# 表外同义探针的改写词（golden oov_syn 组里被改写的目标词）。它们**不得**出现在
# 同义词表成员里——若未来 synonyms 表扩容吃掉其中任何一个，探针即失效，本测试
# 变红提示维护（把该条挪分布或换词）。第 104 刀收编 9 词（OOV_SYN_COLLECTED_WORDS
# ——数据驱动收词，出处/对照见 rag-eval-report 第 104 刀节），从本表移出并改由
# test_golden_oov_syn_collected_words_in_table 反向钉住。
OOV_SYN_REWRITE_WORDS = (
    "退回去",
    "存放",
    "商品编码",
    "哪家厂",
    "毛球",
    "发出来",
)

# 第 104 刀收编的探针词（(左=语料活词, 右=收词) 对组——见 synonyms._PAIR_GROUPS）。
# 已收编的探针不再是「表外」自由测量值，而是表内回归数字（收词前 40.0% -> 86.7%）。
OOV_SYN_COLLECTED_WORDS = (
    ("刻字", "雕字"),
    ("上市年份", "哪一年出"),
    ("上班", "开门"),
    ("配送", "寄出"),
    ("保修", "给修"),
    ("发票", "票据"),
    ("退换", "退掉"),
    ("运费", "邮资"),
    ("保温杯", "保温壶"),
)


def test_golden_schema_distributions() -> None:
    """golden_large.json 形状自检（纯文件检查，不连 DB）：六分布键合法、
    id/问句唯一、oov_syn 改写词不在同义词表内。"""
    golden = EVAL_DIR / "out" / "golden_large.json"
    if not golden.exists():
        pytest.skip("golden 大集文件不在本环境（生成见 scripts/eval/generate_golden.py）")
    cases = json.loads(golden.read_text(encoding="utf-8"))

    valid = {"positive", "paraphrase", "confusion", "refusal", "oov_syn", "sem_neg"}
    ids = [c["id"] for c in cases]
    questions = [c["question"] for c in cases]
    assert len(ids) == len(set(ids)), "case id 必须唯一"
    assert len(questions) == len(set(questions)), "问句分布间不得重复"
    assert {c["distribution"] for c in cases} <= valid, "分布词表外的键"

    for case in cases:
        expect = case["expect"]
        if case["distribution"] == "refusal":
            assert expect == {"refuse": True}, f"{case['id']} 拒答期望形状"
        else:
            assert set(expect) == {"cite"}, f"{case['id']} cite 期望只有 cite 键"
            assert set(expect["cite"]) == {"asset_id", "version_no"}
            assert isinstance(expect["cite"]["asset_id"], int)
            assert isinstance(expect["cite"]["version_no"], int)

    # 第五分布必须在场（第 101 刀起大集含表外同义探针 10-20 条）
    oov_cases = [c for c in cases if c["distribution"] == "oov_syn"]
    assert 10 <= len(oov_cases) <= 20, f"表外同义探针 10-20 条，实际 {len(oov_cases)}"


def test_golden_schema_sem_neg_shape() -> None:
    """第六分布 sem_neg 形状自检（第 107a 刀）：25-30 条、id 全部 sneg- 前缀、
    两亚族构成钉住（否定 sneg-neg- 10-12 条 + 近邻 sneg-nb- 12-18 条）——
    亚族配比是评测靶子的构成申报，改配比须同步改报告。"""
    golden = EVAL_DIR / "out" / "golden_large.json"
    if not golden.exists():
        pytest.skip("golden 大集文件不在本环境（生成见 scripts/eval/generate_golden.py）")
    cases = json.loads(golden.read_text(encoding="utf-8"))
    sem_neg = [c for c in cases if c["distribution"] == "sem_neg"]
    assert 25 <= len(sem_neg) <= 30, f"语义负例 25-30 条，实际 {len(sem_neg)}"
    for case in sem_neg:
        assert case["id"].startswith("sneg-"), f"{case['id']} 必须 sneg- 前缀"
        assert set(case["expect"]) == {"cite"}, "sem_neg 全部是 cite 期望（与正向同锚/对象锚）"
    negation = [c for c in sem_neg if c["id"].startswith("sneg-neg-")]
    near = [c for c in sem_neg if c["id"].startswith("sneg-nb-")]
    assert 10 <= len(negation) <= 12, f"否定语义 10-12 条，实际 {len(negation)}"
    assert 12 <= len(near) <= 18, f"语义近邻 12-18 条，实际 {len(near)}"
    # 否定式问句必须真带否定标记（不/没/别/未）——形态钉住，防止未来维护混入正向句
    for case in negation:
        assert any(mark in case["question"] for mark in ("不", "没", "别", "未")), (
            f"{case['id']} 否定式问句缺否定标记：{case['question']!r}"
        )


def test_golden_oov_syn_words_outside_synonym_table() -> None:
    """oov_syn 组里**未收编**的改写词必须仍在同义词表之外（表内词会被检索侧
    并集扩展救回，探针就不再测「表外泛化」）。"""
    members = {m for group in SYNONYM_GROUPS for m in group}
    members |= {"折扣券", "优惠券"}
    for word in OOV_SYN_REWRITE_WORDS:
        assert word not in members, f"{word!r} 已进同义词表——表外探针失效，需换词"


def test_golden_oov_syn_collected_words_in_table() -> None:
    """第 104 刀收编的 9 个探针词必须在对组里（左=语料活词、右=收词）——反向
    钉住收词成果不被误删；形态钉 _PAIR_GROUPS（影蔽语义），不进环组。"""
    from suite_api.services.synonyms import _PAIR_GROUPS

    for pair in OOV_SYN_COLLECTED_WORDS:
        assert pair in _PAIR_GROUPS, f"{pair} 不在 _PAIR_GROUPS——第 104 刀收词被删或改形"


# ---------------------------------------------------------------- 第 102 刀：--judge-llm 生成路径观察


def _msg(
    mid: int, sid: int, role: str, content: str, kind: str | None = None, citations=None
) -> dict:
    return {
        "id": mid,
        "session_id": sid,
        "role": role,
        "content": content,
        "kind": kind,
        "citations": citations,
    }


def test_is_template_answer_matches_fallback_prefixes() -> None:
    """降级模板形状判定：compose_answer 两类前缀是模板路径指纹。"""
    assert runner.is_template_answer("根据已发布的规格文档《钛钢保温杯 · 规格》：净含量：500ml。")
    assert runner.is_template_answer("根据已发布的客服对话记录：顾客：您好")
    assert not runner.is_template_answer("净含量为500ml。")
    assert not runner.is_template_answer("")


def test_build_generated_samples_pairs_filters_and_dedupes() -> None:
    """存量样本构造：answer+引用入集、模板形状剔除、同问同答去重、跨会话配对。"""
    messages = [
        _msg(1, 1, "customer", "保温杯的净含量是多少"),
        _msg(2, 1, "agent", "净含量为500ml。", "answer", [{"asset_id": 9, "version_no": 1}]),
        # 同问同答的重复探针：只留首条
        _msg(3, 1, "customer", "保温杯的净含量是多少"),
        _msg(4, 1, "agent", "净含量为500ml。", "answer", [{"asset_id": 9, "version_no": 1}]),
        # 同问不同答（LLM 非确定性）：两条都留
        _msg(5, 1, "customer", "保温杯的净含量是多少"),
        _msg(6, 1, "agent", "该保温杯的净含量是 500ml。", "answer", [{"asset_id": 9, "version_no": 1}]),
        # 模板回落形状（fallback 指纹）：剔除
        _msg(7, 1, "customer", "材质是什么"),
        _msg(
            8, 1, "agent", "根据已发布的规格文档《钛钢保温杯 · 规格》：材质：316不锈钢。",
            "answer", [{"asset_id": 3, "version_no": 1}],
        ),
        # citations 空（工具/目录模板面）：剔除
        _msg(9, 1, "customer", "你们卖什么"),
        _msg(10, 1, "agent", "本店在售商品共 3 件", "answer", []),
        # refusal：剔除（87 刀口径只评 answered）
        _msg(11, 1, "customer", "有赠品吗"),
        _msg(12, 1, "agent", "抱歉，已发布资产里没有能回答这个问题的证据。", "refusal", []),
        # 跨会话：会话 2 的问句配会话 2 的回答（不受会话 1 末问污染）
        _msg(13, 2, "customer", "退货政策是什么"),
        _msg(14, 2, "agent", "支持7天无理由退货。", "answer", [{"asset_id": 479, "version_no": 1}]),
    ]
    samples = runner.build_generated_samples(messages)
    assert [(s["message_id"], s["question"]) for s in samples] == [
        (2, "保温杯的净含量是多少"),
        (6, "保温杯的净含量是多少"),
        (14, "退货政策是什么"),
    ]
    assert all(s["source"] == "存量" and s["citations"] for s in samples)


def test_judge_llm_summary_denominator_only_judged() -> None:
    """汇总口径与 87 刀一致：分母只算评上的，未评上单列。"""
    summary = runner.judge_llm_summary(3, [(True, "依据齐全。"), (False, "编造。"), None])
    assert summary == {
        "samples": 3,
        "judged": 2,
        "supported": 1,
        "failed": 1,
        "supported_rate": 0.5,
    }
    assert runner.judge_llm_summary(0, [])["supported_rate"] is None


def test_reason_first_sentence() -> None:
    assert runner.reason_first_sentence("「支持7天无理由」未在证据出现。另有第二句。") == (
        "「支持7天无理由」未在证据出现。"
    )
    assert runner.reason_first_sentence("只有一句没有终止符") == "只有一句没有终止符"
    assert runner.reason_first_sentence("第一行\n第二行") == "第一行"


def test_reason_from_raw_json_and_fallback() -> None:
    assert runner._reason_from_raw('{"supported": false, "reason": "第二句编造"}') == "第二句编造"
    assert runner._reason_from_raw("没有 JSON 的原始输出\n第二行") == "没有 JSON 的原始输出"


def test_judge_llm_with_retry_recovers_and_fails_soft(monkeypatch) -> None:
    """87 刀同款重试纪律：抖动内恢复 -> (verdict, reason)；重试尽 -> None。"""
    from suite_api.services.llm import LLMUnavailable

    calls: list[int] = []

    def fake_judge_llm_one(question: str, chunks, answer: str) -> tuple[bool, str]:
        calls.append(1)
        if len(calls) < runner.JUDGE_ATTEMPTS:
            raise LLMUnavailable("网关抖动")
        return (False, "首句无原文依据。")

    monkeypatch.setattr(runner, "judge_llm_one", fake_judge_llm_one)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    assert runner.judge_llm_with_retry("Q", ["证据"], "回答") == (False, "首句无原文依据。")
    assert len(calls) == runner.JUDGE_ATTEMPTS

    def always_fail(question: str, chunks, answer: str) -> tuple[bool, str]:
        raise LLMUnavailable("网关持续不可用")

    monkeypatch.setattr(runner, "judge_llm_one", always_fail)
    assert runner.judge_llm_with_retry("Q", ["证据"], "回答") is None


def test_judge_llm_report_lines_verdicts_and_caveats() -> None:
    """报告装配（纯函数）：逐条 verdict 带原因首句、未评上注记、口径声明在案。"""
    samples = [
        {
            "source": "存量",
            "message_id": 2,
            "session_id": 1,
            "question": "净含量是多少",
            "answer": "净含量为500ml。",
            "citations": [{"asset_id": 9, "version_no": 1}],
        },
        {
            "source": "新问",
            "message_id": 900,
            "session_id": 77,
            "question": "退货运费多少钱",
            "answer": "运费由商家承担。",
            "citations": [{"asset_id": 479, "version_no": 1}],
        },
        {
            "source": "存量",
            "message_id": 5,
            "session_id": 2,
            "question": "会员积分怎么兑换",
            "answer": "100积分抵1元。",
            "citations": [{"asset_id": 106, "version_no": 1}],
        },
    ]
    verdicts = [
        (True, "每句均有原文依据。"),
        (False, "「运费由商家承担」未在证据出现。第二句另有问题。"),
        None,
    ]
    report = runner.judge_llm_report(
        samples, verdicts, fresh_stats={"asked": 10, "generated": 8, "refusal": 2}, db_url="pg://x"
    )
    assert "存量生成消息 2 条" in report and "现场真问 1 条" in report
    assert "问 10 条" in report and "生成作答 8" in report and "拒答 2" in report
    assert "supported——每句均有原文依据。" in report
    assert "False——「运费由商家承担」未在证据出现。" in report
    assert "未评上（重试尽，不计入分母）" in report
    assert "汇总：评上 2/3（未评上 1），supported 1 条，supported 率 50.0%" in report
    assert "当日同一网关" in report and "不进 CI" in report


def test_fresh_questions_are_rag_path_shapes() -> None:
    """现场真问清单形状自检：8-10 条、无订单号/库存词/转人工（纯 RAG 问法）。"""
    assert 8 <= len(runner.FRESH_QUESTIONS) <= 10
    for question in runner.FRESH_QUESTIONS:
        assert "SO-" not in question
        assert "人工" not in question and "转接" not in question
