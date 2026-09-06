"""机洗抽取单元测试（不依赖 DB）：正则边界 + 弃权路径。

关键回归：原型第五轮教训——「未标注材质牌号」不得被抽成「牌号」；
禁止空字符串冒充（0009 弃权必须显式）。
"""

import pytest

from suite_api.services.machine_wash import (
    extract_document_fields,
    extract_material,
    extract_net_content,
    extract_shelf_life,
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


# ---------- 材质（禁止裸通配） ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("杯身材质：304不锈钢", "304不锈钢"),
        ("材质为食品级硅胶。", "食品级硅胶"),
        ("材质是钛钢，经久耐用", "钛钢"),
        ("杯身采用304不锈钢材质制成", "304不锈钢"),  # 后缀式，剥离引导动词
        ("钛钢材质", "钛钢"),
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
