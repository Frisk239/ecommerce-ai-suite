"""发布闸门与取值规则（0010/0019）：纯逻辑，无框架依赖，便于单测。

必填集合运行时由所挂商品的 spec_schema 派生（0019），不是列；
不挂商品（或不挂商品的文档）规格必填为空集，人洗通过即可发布。

缺项分两类报（原型 UX-NOTES 二.3 的取舍，API 层再校验而非仅前端）：
- missing：必填字段弃权/未填，连机洗值都没有——「缺少必填字段」；
- unconfirmed：机洗抽到了值但操作者没确认——「待确认字段」。
"""

from typing import Any


def _effective_value(entry: Any) -> str | None:
    if isinstance(entry, dict):
        value = entry.get("value")
        if isinstance(value, str) and value.strip():
            return value
    return None


def confirmed_value(confirmed: dict[str, Any], field: str) -> str | None:
    return _effective_value(confirmed.get(field))


def machine_value(extracted: dict[str, Any], field: str) -> str | None:
    entry = extracted.get(field)
    if isinstance(entry, dict) and entry.get("abstained") is True:
        return None  # 显式弃权：不算抽到（0009）
    return _effective_value(entry)


def resolve_field_value(
    extracted: dict[str, Any], confirmed: dict[str, Any], field: str
) -> str | None:
    """合并取值口径：confirmed 优先，其次 extracted 中非弃权值（单字段查询用）。"""
    return confirmed_value(confirmed, field) or machine_value(extracted, field)


def required_fields(spec_schema: dict[str, Any]) -> list[str]:
    return [
        field
        for field, rule in (spec_schema or {}).items()
        if isinstance(rule, dict) and rule.get("required") is True
    ]


def schema_field_names(spec_schema: dict[str, Any]) -> list[str]:
    return list((spec_schema or {}).keys())


def evaluate_publish_gate(
    spec_schema: dict[str, Any],
    extracted: dict[str, Any],
    confirmed: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """返回 (missing, unconfirmed)。两者皆空 = 闸门通过。"""
    missing: list[str] = []
    unconfirmed: list[str] = []
    for field in required_fields(spec_schema):
        if confirmed_value(confirmed, field) is not None:
            continue
        if machine_value(extracted, field) is not None:
            unconfirmed.append(field)
        else:
            missing.append(field)
    return missing, unconfirmed


def publishable_values(
    spec_schema: dict[str, Any],
    extracted: dict[str, Any],
    confirmed: dict[str, Any],
) -> dict[str, str]:
    """写回商品的字段值（0010 写回语义）：confirmed 的键 ∩ schema 的键。

    只写操作者确认（机洗值转确认）或补填（人填值）落下的字段；未确认的
    机洗值不写回，商品该字段保持旧值。extracted 仅为保持三参调用签名保留，
    不参与写回。必填闸门（missing/unconfirmed 两类 422）见 evaluate_publish_gate，
    不受此处影响。
    """
    values: dict[str, str] = {}
    for field in schema_field_names(spec_schema):
        value = confirmed_value(confirmed, field)
        if value is not None:
            values[field] = value
    return values
