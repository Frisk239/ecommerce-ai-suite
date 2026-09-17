"""第 33 刀：类目规格模板 + 真商品闸门/写回。"""

from suite_api.services.category_schema import SCHEMA_BY_CATEGORY, schema_for_category
from suite_api.services.publishing import evaluate_publish_gate, publishable_values


def test_known_categories_have_required_fields() -> None:
    for category in (
        "食品",
        "器皿",
        "智能手机",
        "笔记本电脑",
        "平板电脑",
        "电视机",
        "洗衣机",
        "图书",
        "家具",
        "键盘",
        "鼠标",
        "显示器",
        "耳机",
    ):
        schema = schema_for_category(category)
        assert schema, category
        assert any(rule.get("required") for rule in schema.values()), category
        assert schema == SCHEMA_BY_CATEGORY[category]


def test_digital_peripherals_brand_required_others_optional() -> None:
    """第 90 刀：数码四类品牌（P176）必填；高/宽/上市年份覆盖稀疏不设闸。

    第 94a 刀（审计 18 归位）：四类各加 `图片`（P18）/`官网`（P856）两个**可选**
    字段位——Wikidata 有图商品回填用，不设必填、不进发布闸门。"""
    assert schema_for_category("键盘") == {
        "品牌": {"required": True},
        "图片": {"required": False},
        "官网": {"required": False},
    }
    assert schema_for_category("鼠标") == {
        "品牌": {"required": True},
        "图片": {"required": False},
        "官网": {"required": False},
    }
    monitor = schema_for_category("显示器")
    assert monitor["品牌"] == {"required": True}
    assert monitor["高度"] == {"required": False}
    assert monitor["宽度"] == {"required": False}
    assert monitor["图片"] == {"required": False}
    assert monitor["官网"] == {"required": False}
    headphone = schema_for_category("耳机")
    assert headphone["品牌"] == {"required": True}
    assert headphone["上市年份"] == {"required": False}
    # 品牌缺失拦发布；可选字段缺失/未确认不拦（evaluate_publish_gate 只看必填）
    missing, unconfirmed = evaluate_publish_gate(monitor, {}, {})
    assert missing == ["品牌"] and unconfirmed == []
    # 可选字段（图片/官网）弃权/未确认同样不拦、也不进写回（只写 confirmed）
    assert publishable_values(monitor, {}, {"品牌": {"value": "戴尔", "source": "human"}}) == {
        "品牌": "戴尔"
    }


def test_unknown_category_empty_schema() -> None:
    assert schema_for_category("不存在的类目") == {}


def test_off_dump_schema_does_not_require_invented_shelf_life() -> None:
    """OFF 灌入的商品只要求 dump 里有的净含量，不把保质期写进 schema。"""
    schema = {"净含量": {"required": True}}
    extracted = {"净含量": {"value": "550ml", "source": "machine"}}
    confirmed = {"净含量": {"value": "550ml", "source": "human"}}
    missing, unconfirmed = evaluate_publish_gate(schema, extracted, confirmed)
    assert missing == [] and unconfirmed == []


def test_phone_schema_gate_missing_brand() -> None:
    schema = schema_for_category("智能手机")
    missing, unconfirmed = evaluate_publish_gate(schema, {}, {})
    assert missing == ["品牌"]
    assert unconfirmed == []


def test_phone_schema_writeback_only_confirmed() -> None:
    schema = schema_for_category("智能手机")
    extracted = {"品牌": {"value": "Apple", "source": "machine"}}
    confirmed = {"品牌": {"value": "Apple", "source": "human"}}
    missing, unconfirmed = evaluate_publish_gate(schema, extracted, confirmed)
    assert missing == [] and unconfirmed == []
    assert publishable_values(schema, extracted, confirmed) == {"品牌": "Apple"}
