"""销售考核单元测试（不依赖 DB，LLM 以替身注入不发外网；第 19 刀/ADR 0040）。

覆盖纯逻辑三块：
- 题库推导展开/兜底：confirmed qa_pairs 逐对成题（题源锚含 pair_index）；
  弃权/空/无项 -> 转写首个「顾客：」行兜底（standard_answer=None）；全坏形状、
  转写无顾客行、存储异常 -> 不成题；
- 评分解析 parse_score_output（好 JSON/围栏/坏 JSON/超区间/bool/缺 comment）
  与打分三态 try_score（成功 / LLM 未配置 / LLM 故障 / 坏输出 -> 未评分原因）；
- rubric prompt 三要素（口径准确 40/证据贴合 30/服务语气 30 + JSON 输出契约）
  与题面/标准答案/受训者答案进 user prompt。
"""

from typing import Any

import pytest

from suite_api.models import Asset, AssetVersion
from suite_api.services import llm as llm_module
from suite_api.services.coaching import (
    RUBRIC_MAX,
    SCORING_SYSTEM_PROMPT,
    ScoreParseError,
    asset_questions,
    build_score_prompt,
    first_customer_question,
    normalize_key,
    parse_score_output,
    try_score,
)

# ---------- 替身 ----------


class _FakeStorage:
    """最小对象存储面：按 key 回字节；缺失抛 FileNotFoundError（与 LocalDirectoryStorage 同契约）。"""

    def __init__(self, blobs: dict[str, bytes] | None = None) -> None:
        self.blobs = blobs or {}

    def get_bytes(self, key: str) -> bytes:
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]


def _asset(asset_id: int = 7, title: str | None = "客服对话 · S-3") -> Asset:
    return Asset(
        id=asset_id,
        kind="dialogue",
        status="published",
        source_kind="session_backflow",
        title=title,
    )


def _version(
    confirmed: dict[str, Any], version_no: int = 1, object_key: str = "dialogue/k.txt"
) -> AssetVersion:
    return AssetVersion(
        asset_id=7,
        version_no=version_no,
        object_key=object_key,
        extracted_fields={},
        confirmed_fields=confirmed,
    )


def _qa_entry(pairs: list[dict[str, str]]) -> dict[str, Any]:
    return {"qa_pairs": {"value": pairs, "source": "human"}}


TRANSCRIPT = "顾客：盲盒可以指定款式吗\n客服：盲盒随机发货，不能指定\n顾客：\n客服：……"

STORAGE = _FakeStorage({"dialogue/k.txt": TRANSCRIPT.encode("utf-8")})


# ---------- 题库展开：confirmed qa_pairs 逐对成题 ----------


def test_expand_confirmed_qa_pairs() -> None:
    questions = asset_questions(
        None,  # read_version_text 不消费 session（兜底路径才不会走到）
        _asset(),
        _version(_qa_entry([{"q": "支持海外配送吗", "a": "暂不支持海外配送地址"}])),
        STORAGE,
    )
    assert questions == [
        {
            "key": {"asset_id": 7, "version_no": 1, "source": "qa", "pair_index": 0},
            "question": "支持海外配送吗",
            "standard_answer": "暂不支持海外配送地址",
            "asset_title": "客服对话 · S-3",
        }
    ]


def test_expand_uses_current_published_version_number() -> None:
    # 版本行即当前指针版（derive_questions 取指针行喂进来），锚上的 version_no 跟版本走
    questions = asset_questions(
        None, _asset(), _version(_qa_entry([{"q": "问", "a": "答"}]), version_no=2), STORAGE
    )
    assert questions[0]["key"]["version_no"] == 2


def test_empty_value_or_abstain_or_missing_falls_back_to_transcript() -> None:
    for confirmed in (
        {"qa_pairs": {"value": [], "source": "human"}},  # 人洗确认「没有 QA」
        {"qa_pairs": {"abstained": True}},  # 机洗弃权原样带着发布
        {},  # 草稿未确认直接发布（confirmed 无 qa_pairs）
    ):
        questions = asset_questions(None, _asset(), _version(confirmed), STORAGE)
        assert questions == [
            {
                "key": {"asset_id": 7, "version_no": 1, "source": "transcript", "pair_index": None},
                "question": "盲盒可以指定款式吗",
                "standard_answer": None,
                "asset_title": "客服对话 · S-3",
            }
        ], confirmed


def test_garbage_pairs_still_fall_back() -> None:
    # 全部项形状不对（q 空/非 dict）-> 展开不出题，兜底转写首问
    bad = {
        "qa_pairs": {
            "value": [{"q": "  ", "a": "答"}, "字符串项", {"a": "无问"}],
            "source": "human",
        }
    }
    questions = asset_questions(None, _asset(), _version(bad), STORAGE)
    assert [q["key"]["source"] for q in questions] == ["transcript"]


def test_machine_draft_in_extracted_is_not_a_question_source() -> None:
    # 只认 confirmed：extracted 里有值也算「未治理过的口径」，走兜底
    version = _version({})
    version.extracted_fields = {
        "qa_pairs": {"value": [{"q": "草稿问", "a": "草稿答"}], "source": "machine"}
    }
    questions = asset_questions(None, _asset(), version, STORAGE)
    assert questions[0]["question"] == "盲盒可以指定款式吗"
    assert questions[0]["key"]["source"] == "transcript"


def test_transcript_without_customer_line_or_missing_object_yields_nothing() -> None:
    empty = _version({}, object_key="dialogue/none.txt")
    assert asset_questions(None, _asset(), empty, STORAGE) == []  # 对象缺失不炸题库
    ok_key = _version({}, object_key="dialogue/plain.txt")
    plain = _FakeStorage({"dialogue/plain.txt": "客服：无人问起\n".encode()})
    assert asset_questions(None, _asset(), ok_key, plain) == []


# ---------- 转写首问提取 ----------


def test_first_customer_question_skips_empty_lines() -> None:
    text = "客服：您好\n顾客：  \n顾客：第二个才是题\n顾客：第三行不算"
    assert first_customer_question(text) == "第二个才是题"


def test_first_customer_question_none() -> None:
    assert first_customer_question("客服：您好，有什么可以帮您") is None


# ---------- 题源锚归一 ----------


def test_normalize_key_valid_and_int_coercion() -> None:
    key = normalize_key({"asset_id": "7", "version_no": 1, "source": "qa", "pair_index": "0"})
    assert key == {"asset_id": 7, "version_no": 1, "source": "qa", "pair_index": 0}
    t = normalize_key({"asset_id": 7, "version_no": 1, "source": "transcript"})
    assert t is not None and t["pair_index"] is None  # transcript 缺省 pair_index=None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "字符串",
        {"version_no": 1, "source": "qa", "pair_index": 0},  # 缺 asset_id
        {"asset_id": 7, "version_no": 1, "source": "guess"},  # 枚举外
        {"asset_id": 7, "version_no": 1, "source": "qa"},  # qa 缺 pair_index
        {"asset_id": "abc", "version_no": 1, "source": "qa", "pair_index": 0},  # 类型错
    ],
)
def test_normalize_key_rejects(raw: Any) -> None:
    assert normalize_key(raw) is None


# ---------- rubric prompt 三要素 ----------


def test_system_prompt_carries_rubric() -> None:
    for token in (
        "口径准确 40",
        "证据贴合 30",
        "服务语气 30",
        "accurate",
        "evidence",
        "tone",
        "comment",
        "JSON",
    ):
        assert token in SCORING_SYSTEM_PROMPT
    assert RUBRIC_MAX == {"accurate": 40, "evidence": 30, "tone": 30}


def test_user_prompt_has_three_inputs() -> None:
    prompt = build_score_prompt("盲盒可以指定款式吗", "盲盒随机发货，不能指定", "亲，盲盒不可以哦")
    assert "盲盒可以指定款式吗" in prompt
    assert "盲盒随机发货，不能指定" in prompt
    assert "亲，盲盒不可以哦" in prompt


def test_user_prompt_fallback_marks_missing_standard() -> None:
    prompt = build_score_prompt("能开发票吗", None, "可以开电子发票")
    assert "无" in prompt and "能开发票吗" in prompt and "可以开电子发票" in prompt


# ---------- 评分解析与三态 ----------

GOOD_SCORE = '{"accurate": 36, "evidence": 25, "tone": 28, "comment": "口径准，语气亲切"}'


def test_parse_plain_and_fenced_score() -> None:
    parsed = parse_score_output(GOOD_SCORE)
    assert parsed == {"accurate": 36, "evidence": 25, "tone": 28, "comment": "口径准，语气亲切"}
    assert parse_score_output(f"```json\n{GOOD_SCORE}\n```") == parsed


@pytest.mark.parametrize(
    "raw",
    [
        "抱歉，我打不了分。",  # 坏 JSON
        "[36, 25, 28]",  # 不是对象
        '{"accurate": 41, "evidence": 25, "tone": 28, "comment": "超区间"}',  # >40
        '{"accurate": -1, "evidence": 25, "tone": 28, "comment": "负数"}',
        '{"accurate": 36, "evidence": 25, "comment": "缺 tone"}',
        '{"accurate": "36", "evidence": 25, "tone": 28, "comment": "字符串分数"}',
        '{"accurate": true, "evidence": 25, "tone": 28, "comment": "bool 不算整数"}',
        '{"accurate": 36, "evidence": 25, "tone": 28, "comment": "   "}',  # 空评语
    ],
)
def test_parse_bad_score_raises(raw: str) -> None:
    with pytest.raises(ScoreParseError):
        parse_score_output(raw)


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def test_try_score_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_llm(monkeypatch, result=GOOD_SCORE)
    score, reason = try_score("题面", "标准答案", "作答")
    assert score == {"accurate": 36, "evidence": 25, "tone": 28, "comment": "口径准，语气亲切"}
    assert reason is None
    assert len(calls) == 1
    assert (
        "题面" in calls[0]["user"] and "标准答案" in calls[0]["user"] and "作答" in calls[0]["user"]
    )
    assert "口径准确 40" in calls[0]["system"]


@pytest.mark.parametrize(
    "kwargs, expect_in_reason",
    [
        (
            {"error": llm_module.LLMNotConfigured("未配置 LLM_API_KEY，厂商生成不可用")},
            "未配置 LLM_API_KEY",
        ),
        ({"error": llm_module.LLMUnavailable("厂商模型暂时不可用")}, "厂商模型暂时不可用"),
        ({"result": "分数是 90 分吧"}, "不是合法 JSON"),
    ],
)
def test_try_score_unscored_states(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict, expect_in_reason: str
) -> None:
    _patch_llm(monkeypatch, **kwargs)
    score, reason = try_score("题面", None, "作答")
    assert score is None
    assert reason is not None and reason.startswith("未评分") and expect_in_reason in reason
