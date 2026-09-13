"""生命周期出口单元测试（第 28 刀/ADR 0042）：三动作状态机 409 边界的纯谓词
+ 对象存储侧「新键写旧键删/逐键删」副作用断言。DB 全链（换字节→发布、放弃
→解锁回滚、废弃→列表隐藏+audit 行）在 test_lifecycle_exits_integration.py。

谓词收口在 routes/assets.py（_can_replace_version_bytes/_is_revision/
_can_discard_asset），模型实例直构即测，无需数据库。
"""

from datetime import UTC, datetime
from pathlib import Path

from suite_api.models import Asset, AssetVersion
from suite_api.routes.assets import (
    _can_discard_asset,
    _can_replace_version_bytes,
    _delete_version_bytes,
    _is_revision,
    _swap_version_bytes,
)
from suite_platform.storage.local import LocalDirectoryStorage


def _asset(
    *,
    status: str = "ingested",
    pointer: int | None = None,
) -> Asset:
    asset = Asset(kind="document", status=status, title="t", source_kind="upload")
    asset.id = 1
    # 指针是 DB 端循环外键：实例上直接赋 id 即可（不 flush）
    asset.current_published_version_id = pointer
    return asset


def _version(
    *,
    version_no: int = 1,
    published: bool = False,
    version_id: int = 10,
    object_key: str = "documents/old/aaaaaaaaaaaaaaaa.txt",
) -> AssetVersion:
    version = AssetVersion(
        asset_id=1,
        version_no=version_no,
        object_key=object_key,
        extracted_fields={},
        confirmed_fields={},
    )
    version.id = version_id
    version.published_at = None if not published else object()  # 非 None 即「已发布」
    return version


# ---------- 换字节闸门：仅未发布且非指针版（线上版 409 的谓词面） ----------


def test_replace_bytes_allows_unpublished_pending_version() -> None:
    # 待人洗首发 v1（published_at 空且指针空）与新登记版同属可换
    assert _can_replace_version_bytes(_asset(status="pending_review"), _version()) is True


def test_replace_bytes_allows_unpublished_revision_on_published_asset() -> None:
    asset = _asset(status="published", pointer=10)
    revision = _version(version_no=2, version_id=11, published=False)
    assert _can_replace_version_bytes(asset, revision) is True


def test_replace_bytes_rejects_published_version() -> None:
    asset = _asset(status="published", pointer=10)
    published_v1 = _version(version_no=1, published=True, version_id=10)
    assert _can_replace_version_bytes(asset, published_v1) is False


def test_replace_bytes_rejects_pointer_version_even_if_unpublished() -> None:
    # 防御口径：published_at 空但恰为指针所指（正常态不会出现）也不许换——
    # 线上正在服务的字节不可触碰是硬边界
    asset = _asset(status="published", pointer=10)
    assert _can_replace_version_bytes(asset, _version(version_no=2, version_id=10)) is False


# ---------- 修订判定：v1 未发布新资产不算「修订」，引导走废弃 ----------


def test_v1_unpublished_new_asset_is_not_revision() -> None:
    assert _is_revision(_asset(pointer=None), _version(version_no=1)) is False


def test_unpublished_v2_is_revision() -> None:
    asset = _asset(status="published", pointer=10)
    assert _is_revision(asset, _version(version_no=2, version_id=11)) is True


def test_v1_with_publish_history_is_revision() -> None:
    # 指针非空即资产曾发布过：此时未发布 v1 只能是（历史删剩的）修订语境
    assert _is_revision(_asset(pointer=99), _version(version_no=1)) is True


# ---------- 废弃闸门：已接入且从未发布 ----------


def test_discard_allows_ingested_never_published() -> None:
    assert _can_discard_asset(_asset(status="ingested"), has_published_version=False) is True


def test_discard_rejects_when_any_version_was_published() -> None:
    # 指针可能已回退到空？不——指针从不清空；但历史版本 published_at 非空即有
    # 已发布历史，权威证据不可抹
    assert _can_discard_asset(_asset(status="ingested"), has_published_version=True) is False


def test_discard_rejects_non_ingested_status() -> None:
    # 待人洗/已发布资产不是失败资产：仍可走人洗/发布，不给删除退路
    assert (
        _can_discard_asset(_asset(status="pending_review"), has_published_version=False) is False
    )
    assert _can_discard_asset(_asset(status="published"), has_published_version=False) is False


def test_discard_rejects_when_pointer_set() -> None:
    # 指针非空=线上在服务（或曾服务）：has_published_version 再兜一层
    assert _can_discard_asset(_asset(status="ingested", pointer=10), has_published_version=False) is False


# ---------- 对象存储侧副作用：新键写、旧键删（孤儿清理） ----------


def test_swap_version_bytes_writes_new_key_and_deletes_old(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    old_key = "documents/old/aaaaaaaaaaaaaaaa.txt"
    storage.put_bytes(old_key, b"old-bytes")
    version = _version(object_key=old_key)

    new_key = _swap_version_bytes(storage, "document", version, b"new-bytes")

    assert version.object_key == new_key != old_key  # 0003 键不复用：每版一把新键
    assert storage.get_bytes(new_key) == b"new-bytes"
    assert not storage.exists(old_key)  # 旧修订键字节已删（不再有孤儿）


def test_swap_version_bytes_key_shape_follows_kind(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    version = _version(object_key="dialogue/old/bbbbbbbbbbbbbbbb.txt")
    new_key = _swap_version_bytes(storage, "dialogue", version, b"transcript")
    assert new_key.startswith("dialogue/")
    assert storage.exists(new_key)


def test_delete_version_bytes_removes_every_key(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    keys = ["documents/k1/cccccccccccccccc.txt", "documents/k2/dddddddddddddddd.txt"]
    for key in keys:
        storage.put_bytes(key, b"x")
    versions = [_version(object_key=key) for key in keys]

    _delete_version_bytes(storage, versions)

    assert not any(storage.exists(key) for key in keys)


def test_delete_version_bytes_idempotent_on_missing_key(tmp_path: Path) -> None:
    # storage.delete 幂等：键不存在不抛（废弃路径对悬空键安全）
    storage = LocalDirectoryStorage(tmp_path)
    _delete_version_bytes(storage, [_version(object_key="documents/gone/eeeeeeeeeeeeeeee.txt")])


def test_can_publish_rejects_discarded_asset() -> None:
    """第 84 刀：已废弃资产不可再发布（83 刀评审记债）。

    否则「废弃=隐藏」（0042）与「可发新版」冲突——发出去的新版在检索/导出面
    因 discarded 过滤恒不可见，治理上是黑洞（操作者见发布成功、顾客永查不到）。
    """
    from suite_api.routes.assets import _can_publish

    version = _version(published=False)
    # 已发布资产上的未发布修订：正常可发
    live = _asset(status="published", pointer=1)
    assert _can_publish(live, version) is True
    # 同一形态 + 已废弃 -> 拒绝
    live.discarded_at = datetime.now(UTC)
    assert _can_publish(live, version) is False
