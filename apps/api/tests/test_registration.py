"""登记共享服务单元测试：source_kind 枚举校验（0025：服务端定值，应用层把关）
+ 机洗字段集按种类分派（第 12 刀/ADR 0035：dialogue -> qa_pairs）。

幂等缺口判定属 DB 交互（SELECT 同文 open 缺口），无可提炼的无副作用纯函数，
由集成测试钉死（test_service_integration 的缺口契约组）。
"""

import pytest

from suite_api.models import Product
from suite_api.services.registration import (
    SOURCE_KINDS,
    machine_wash_field_names,
    register_asset,
    validate_source_kind,
)


@pytest.mark.parametrize("source_kind", SOURCE_KINDS)
def test_all_source_kinds_accepted(source_kind: str) -> None:
    assert validate_source_kind(source_kind) == source_kind


@pytest.mark.parametrize(
    "bad",
    ["", "upload ", "上传", "Session_Backflow", "session-backflow", "unknown"],
)
def test_bad_source_kind_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="来源种类"):
        validate_source_kind(bad)


def test_register_asset_validates_source_before_any_io() -> None:
    """坏来源在碰存储/数据库之前失败：可无 db/storage 直调（路由层转 422）。"""
    with pytest.raises(ValueError, match="来源种类"):
        register_asset(
            None,  # type: ignore[arg-type]  # 校验先于任何 DB 访问
            None,  # type: ignore[arg-type]  # 校验先于任何对象存储写入
            kind="document",
            title=None,
            content_bytes=b"x",
            filename=None,
            product_id=None,
            source_kind="bogus",
        )


# ---------- 机洗字段集分派（第 12 刀/ADR 0035） ----------


def test_dialogue_field_set_is_only_qa_pairs() -> None:
    # 对话种类的字段集就是 qa_pairs 一个字段（LLM 抽取），与是否挂商品无关
    assert machine_wash_field_names("dialogue", None) == ["qa_pairs"]


def test_document_field_set_follows_spec_schema() -> None:
    product = Product(
        name="瓶装水",
        category="食品",
        spec_schema={"净含量": {"required": True}, "保质期": {"required": True}},
    )
    assert machine_wash_field_names("document", product) == ["净含量", "保质期"]


def test_unlinked_document_field_set_is_empty() -> None:
    assert machine_wash_field_names("document", None) == []


def test_document_field_set_drops_qa_pairs_name_collision() -> None:
    """spec_schema 撞名防御：QA 是种类级语义（kind=对话才有 qa_pairs），
    文档字段集即便 schema 混入 qa_pairs 键也滤掉——机洗与人洗闸门都不放行。"""
    product = Product(name="怪键", category="测试", spec_schema={"净含量": {}, "qa_pairs": {}})
    assert machine_wash_field_names("document", product) == ["净含量"]
