"""机洗抽取单元测试（不依赖 DB）：正则边界 + 弃权路径 + 对话 QA 抽取（第 12 刀）。

关键回归：原型第五轮教训——「未标注材质牌号」不得被抽成「牌号」；
禁止空字符串冒充（0009 弃权必须显式）。QA 抽取只测解析与分级（LLM 以替身注入，
不发外网）：好 JSON=机洗值 / 合法 []=弃权 / 坏输出=MachineWashError 可重试。
"""

from typing import Any

import pytest

from suite_api.services import llm
from suite_api.services.machine_wash import (
    QA_FIELD,
    MachineWashError,
    extract_document_fields,
    extract_material,
    extract_net_content,
    extract_qa_draft,
    extract_shelf_life,
    parse_qa_output,
    run_machine_wash,
)

# ---------- 净含量 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("净含量：550毫升", "550毫升"),
        ("净含量 500 ML", "500ML"),  # 大小写不敏感，保留原文单位形态
        ("净含量：1.5L", "1.5L"),
 ("净含量：2升", "2升"),
        ("净含量 330ml（以标签为准）", "330ml"),
        ("净含量：500克", "500克"),
        ("净含量：0.75千克", "0.75千克"),  # 多字符单位在前，不被截成 "75克"
        ("净含量：750 g", "750g"),
    ],
)
def test_net_content(text: str, expected: str) -> None:
    assert extract_net_content(text) == expected


@pytest.mark.parametrize(
    "text",
    ["未标注净含量", "净含量以实物为准", ""],
)
def test_net_content_abstains(text: str) -> None:
    assert extract_net_content(text) is None


# ---------- 保质期 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("保质期：12个月", "12个月"),  # 整体优先，不是 "12月"
        ("保质期 18 个月", "18个月"),
        ("保质期：30天", "30天"),
        ("保质期 90日", "90日"),
        ("保质期：2年", "2年"),
        ("保质期：6月", "6月"),
    ],
)
def test_shelf_life(text: str, expected: str) -> None:
    assert extract_shelf_life(text) == expected


def test_shelf_life_abstains() -> None:
    assert extract_shelf_life("保质期见包装") is None


def test_shelf_life_skips_date_traps() -> None:
    # 日期陷阱：日期类片段（生产日期/出厂日期/批号/日期 + 数字年月日）不是保质期
    assert extract_shelf_life("生产日期：2026年8月1日，保质期：12个月") == "12个月"
    assert extract_shelf_life("生产日期 2026 年 6 月，保质期 90日") == "90日"
    assert extract_shelf_life("出厂日期：2026年8月1日") is None  # 只有日期：弃权


# ---------- 材质（禁止裸通配） ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("杯身材质：304不锈钢", "304不锈钢"),
        ("材质为食品级硅胶。", "食品级硅胶"),
        ("材质是钛钢，经久耐用", "钛钢"),
        ("杯身采用304不锈钢材质制成", "304不锈钢"),  # 后缀式，剥离引导动词
        ("钛钢材质", "钛钢"),
        ("材质：316不锈钢", "316不锈钢"),  # 分隔式过黑名单后的回归：正常值照抽
    ],
)
def test_material_explicit_patterns(text: str, expected: str) -> None:
    assert extract_material(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "未标注材质牌号",  # 教训用例：不得抽出「牌号」，也不得编造「标注」
        "材质牌号未标注，详见吊牌",
        "材质信息详见外包装",
        "高硼硅玻璃，双层结构",  # 有材质词但无显式声明模式：弃权而非裸通配
        "材质：未标注材质信息",  # 分隔式同过否定词黑名单：以「未标注」开头，弃权
        "材质为不详",  # 分隔式命中「不详」：弃权
        "",
    ],
)
def test_material_abstains(text: str) -> None:
    assert extract_material(text) is None


# ---------- 字段集合驱动 + 弃权标记 ----------


def test_extract_fields_by_schema_keys() -> None:
    text = "净含量：550毫升\n保质期：12个月"
    result = extract_document_fields(text, ["净含量", "保质期", "材质"])
    assert result["净含量"] == {"value": "550毫升", "source": "machine"}
    assert result["保质期"] == {"value": "12个月", "source": "machine"}
    assert result["材质"] == {"abstained": True}


def test_extract_fields_empty_schema_returns_empty() -> None:
    # 不挂商品的文档：字段集合为空集，机洗无字段抽取
    assert extract_document_fields("任意文本", []) == {}


def test_unknown_field_abstains() -> None:
    # 未来类目扩展的新字段没有抽取器：弃权，留给补填
    result = extract_document_fields("产地：杭州", ["产地"])
    assert result == {"产地": {"abstained": True}}


def test_abstention_is_explicit_never_empty_string() -> None:
    result = extract_document_fields("无任何规格信息", ["净含量"])
    assert result["净含量"] == {"abstained": True}
    assert "value" not in result["净含量"]  # 弃权显式留空，无 value 键可冒充


# ---------- 对话 QA 抽取：输出解析（纯函数，三分支的好/坏侧） ----------


def test_parse_qa_output_accepts_plain_and_fenced_json() -> None:
    good = '[{"q": "退货要留吊牌吗", "a": "需要保持吊牌完整"}, {"q": "几天到账", "a": "3个工作日"}]'
    expected = [{"q": "退货要留吊牌吗", "a": "需要保持吊牌完整"}, {"q": "几天到账", "a": "3个工作日"}]
    assert parse_qa_output(good) == expected
    assert parse_qa_output(f"```json\n{good}\n```") == expected  # 围栏剥离
    assert parse_qa_output(f"```{good}```") == expected  # 无语言标注/无换行也剥
    assert parse_qa_output("  \n [] \n ") == []  # 合法空数组：无可抽 QA（调用方弃权）
    assert parse_qa_output('[{"q": " 问题 ", "a": " 回答 "}]') == [{"q": "问题", "a": "回答"}]


@pytest.mark.parametrize(
    "bad",
    [
        "这不是JSON",
        '{"q": "对象不是数组"}',
        "[{}]",
        '[{"q": "有问题", "a": "  "}]',  # 答为空串=坏输出（0009 禁空串口径）
        '[{"q": 1, "a": "答"}]',
        "[null]",
        "",
    ],
)
def test_parse_qa_output_rejects_malformed(bad: str) -> None:
    with pytest.raises(MachineWashError, match="LLM QA 抽取失败"):
        parse_qa_output(bad)


# ---------- 对话 QA 抽取：分级（未配置=弃权降级；失败/坏输出=可重试失败） ----------


def _patch_complete_chat(monkeypatch: pytest.MonkeyPatch, result: str | None = None, error: Exception | None = None) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> str:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm, "complete_chat", fake)
    return calls


_TRANSCRIPT = "顾客：退货要留吊牌吗\n客服：需要保持吊牌完整才能退货"


def test_extract_qa_draft_returns_machine_value(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_complete_chat(
        monkeypatch, result='```json\n[{"q": "退货要留吊牌吗", "a": "需保持吊牌完整"}]\n```'
    )
    entry = extract_qa_draft(_TRANSCRIPT)
    assert entry == {"value": [{"q": "退货要留吊牌吗", "a": "需保持吊牌完整"}], "source": "machine"}
    assert len(calls) == 1
    assert _TRANSCRIPT in calls[0]["user"]  # 转写全文进 prompt
    assert "JSON" in calls[0]["system"]


def test_extract_qa_draft_empty_array_abstains(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_complete_chat(monkeypatch, result="[]")
    assert extract_qa_draft(_TRANSCRIPT) == {"abstained": True}


def test_extract_qa_draft_not_configured_degrades_to_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 key（第 3 刀行为保留）：弃权降级、对话照常推进待人洗，不算失败。"""
    _patch_complete_chat(monkeypatch, error=llm.LLMNotConfigured("未配置 LLM_API_KEY"))
    assert extract_qa_draft(_TRANSCRIPT) == {"abstained": True}


def test_extract_qa_draft_llm_failure_is_retryable_wash_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_complete_chat(monkeypatch, error=llm.LLMUnavailable("厂商模型暂时不可用"))
    with pytest.raises(MachineWashError, match="LLM QA 抽取失败"):
        extract_qa_draft(_TRANSCRIPT)


def test_extract_qa_draft_bad_output_is_retryable_wash_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_complete_chat(monkeypatch, result="模型开始自由发挥：我觉得……")
    with pytest.raises(MachineWashError, match="LLM QA 抽取失败"):
        extract_qa_draft(_TRANSCRIPT)


class _FakeStorage:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_bytes(self, object_key: str) -> bytes:
        return self._data

    def put_bytes(self, object_key: str, data: bytes) -> None:  # pragma: no cover
        raise AssertionError("机洗不写对象存储")


def test_run_machine_wash_dialogue_field_set_uses_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_complete_chat(monkeypatch, result='[{"q": "问", "a": "答"}]')
    result: dict[str, Any] = run_machine_wash(
        _FakeStorage(_TRANSCRIPT.encode()), "dialogue/x/1.txt", [QA_FIELD]
    )
    assert result == {QA_FIELD: {"value": [{"q": "问", "a": "答"}], "source": "machine"}}


def test_run_machine_wash_document_set_never_calls_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_complete_chat(monkeypatch, result="[]")
    result = run_machine_wash(
        _FakeStorage("净含量：550毫升".encode()), "documents/x/1.txt", ["净含量"]
    )
    assert result == {"净含量": {"value": "550毫升", "source": "machine"}}
    assert calls == []  # 文档正则路径不触碰 LLM
