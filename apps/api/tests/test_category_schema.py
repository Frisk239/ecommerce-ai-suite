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
    ):
        schema = schema_for_category(category)
        assert schema, category
        assert any(rule.get("required") for rule in schema.values()), category
        assert schema == SCHEMA_BY_CATEGORY[category]


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
