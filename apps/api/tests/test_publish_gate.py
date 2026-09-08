"""发布闸门纯逻辑单元测试（不依赖 DB）：必填缺项的两类分类（UX-NOTES 二.3）。

- missing：必填字段弃权/未填（连机洗值都没有）；
- unconfirmed：机洗抽到值但操作者未确认。
"""

from suite_api.services.publishing import (
    evaluate_publish_gate,
    publishable_values,
    resolve_field_value,
)

_FOOD_SCHEMA = {"净含量": {"required": True}, "保质期": {"required": True}}
_CUP_SCHEMA = {"净含量": {"required": True}, "材质": {"required": True}}
_MIXED_SCHEMA = {"净含量": {"required": True}, "颜色": {"required": False}}


def test_all_abstained_are_missing() -> None:
    extracted = {"净含量": {"abstained": True}, "材质": {"abstained": True}}
    missing, unconfirmed = evaluate_publish_gate(_CUP_SCHEMA, extracted, {})
    assert missing == ["净含量", "材质"]
    assert unconfirmed == []


def test_unconfirmed_machine_values() -> None:
    extracted = {
        "净含量": {"value": "500ml", "source": "machine"},
        "材质": {"value": "304不锈钢", "source": "machine"},
    }
    missing, unconfirmed = evaluate_publish_gate(_CUP_SCHEMA, extracted, {})
    assert missing == []
    assert unconfirmed == ["净含量", "材质"]


def test_mixed_missing_and_unconfirmed() -> None:
    extracted = {
        "净含量": {"value": "500ml", "source": "machine"},
        "材质": {"abstained": True},
    }
    missing, unconfirmed = evaluate_publish_gate(_CUP_SCHEMA, extracted, {})
    assert missing == ["材质"]
    assert unconfirmed == ["净含量"]


def test_confirmed_overrides_machine_value() -> None:
    extracted = {"净含量": {"value": "500ml", "source": "machine"}}
    confirmed = {"净含量": {"value": "480ml", "source": "human"}}
    missing, unconfirmed = evaluate_publish_gate(_CUP_SCHEMA, extracted, confirmed)
    assert missing == ["材质"]  # 材质仍未处理
    assert unconfirmed == []
    assert resolve_field_value(extracted, confirmed, "净含量") == "480ml"


def test_filling_abstained_field_passes_gate() -> None:
    extracted = {"净含量": {"value": "500ml", "source": "machine"}, "材质": {"abstained": True}}
    confirmed = {
        "净含量": {"value": "500ml", "source": "human"},
        "材质": {"value": "钛钢", "source": "human"},
    }
    missing, unconfirmed = evaluate_publish_gate(_CUP_SCHEMA, extracted, confirmed)
    assert (missing, unconfirmed) == ([], [])


def test_no_product_means_no_required_fields() -> None:
    # 不挂商品的文档：规格必填为空集，人洗通过即可发布
    missing, unconfirmed = evaluate_publish_gate({}, {"净含量": {"abstained": True}}, {})
    assert (missing, unconfirmed) == ([], [])


def test_optional_field_absence_does_not_block() -> None:
    extracted = {"净含量": {"value": "550ml", "source": "machine"}}
    confirmed = {"净含量": {"value": "550ml", "source": "human"}}
    missing, unconfirmed = evaluate_publish_gate(_MIXED_SCHEMA, extracted, confirmed)
    assert (missing, unconfirmed) == ([], [])  # 颜色非必填，缺失不拦


def test_empty_string_confirmed_value_is_not_a_value() -> None:
    # 空字符串不冒充已抽取（0009）：API 层会拦，闸门层也防御
    extracted = {"净含量": {"abstained": True}}
    confirmed = {"净含量": {"value": "   ", "source": "human"}}
    missing, unconfirmed = evaluate_publish_gate(_FOOD_SCHEMA, extracted, confirmed)
    assert "净含量" in missing


def test_inherited_confirmed_counts_as_confirmed() -> None:
    # 修订继承已确认值（标 inherited）不逼重存：闸门视同已确认
    extracted = {
        "净含量": {"value": "550ml", "source": "machine"},
        "保质期": {"value": "12个月", "source": "machine"},
    }
    confirmed = {
        "净含量": {"value": "550ml", "source": "human", "inherited": True},
        "保质期": {"value": "12个月", "source": "human", "inherited": True},
    }
    missing, unconfirmed = evaluate_publish_gate(_FOOD_SCHEMA, extracted, confirmed)
    assert (missing, unconfirmed) == ([], [])
    assert publishable_values(_FOOD_SCHEMA, extracted, confirmed) == {
        "净含量": "550ml",
        "保质期": "12个月",
    }


def test_publishable_values_only_write_confirmed_fields() -> None:
    # 写回 = confirmed 的键 ∩ schema 的键（0010：发布只写操作者确认过的字段）。
    # 未确认的机洗值不写回——含非必填字段，商品该字段保持旧值。
    extracted = {
        "净含量": {"value": "550ml", "source": "machine"},  # 必填，未确认
        "颜色": {"value": "青", "source": "machine"},  # 非必填，未确认
    }
    confirmed = {"材质": {"value": "钛钢", "source": "human"}}  # schema 外：不写
    assert publishable_values(_MIXED_SCHEMA, extracted, confirmed) == {}

    # 确认必填后发布：净含量写回；颜色（非必填）仍未确认，不写回
    confirmed = {**confirmed, "净含量": {"value": "550ml", "source": "human"}}
    assert publishable_values(_MIXED_SCHEMA, extracted, confirmed) == {"净含量": "550ml"}

    # 操作者再确认颜色（机洗值转确认）：非必填字段也一并写回
    confirmed = {**confirmed, "颜色": {"value": "青", "source": "human"}}
    assert publishable_values(_MIXED_SCHEMA, extracted, confirmed) == {
        "净含量": "550ml",
        "颜色": "青",
    }
