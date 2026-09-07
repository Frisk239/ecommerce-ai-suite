"""登记共享服务单元测试：source_kind 枚举校验（0025：服务端定值，应用层把关）。

幂等缺口判定属 DB 交互（SELECT 同文 open 缺口），无可提炼的无副作用纯函数，
由集成测试钉死（test_service_integration 的缺口契约组）。
"""

import pytest

from suite_api.services.registration import (
    SOURCE_KINDS,
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
