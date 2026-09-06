"""LocalDirectoryStorage 冒烟：roundtrip 与键逃逸拒绝。"""

from pathlib import Path

import pytest

from suite_platform.storage import LocalDirectoryStorage, ObjectStorage


def test_roundtrip_put_get_exists_delete(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    key = "assets/2026/09/readme.md"

    storage.put_bytes(key, b"bytes-v1")
    assert storage.exists(key) is True
    assert storage.get_bytes(key) == b"bytes-v1"

    storage.delete(key)
    assert storage.exists(key) is False


def test_get_missing_raises_file_not_found(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    with pytest.raises(FileNotFoundError):
        storage.get_bytes("nope/missing.bin")


def test_delete_missing_is_idempotent(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    storage.delete("nope/missing.bin")  # 不抛即通过


@pytest.mark.parametrize(
    "key",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "/etc/passwd",
        "C:/windows/system32/config",
        "//server/share/x",
        "assets\\..\\escape.txt",
    ],
)
def test_rejects_path_escape(tmp_path: Path, key: str) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    with pytest.raises(ValueError, match="对象键"):
        storage.put_bytes(key, b"x")
    with pytest.raises(ValueError, match="对象键"):
        storage.get_bytes(key)
    with pytest.raises(ValueError, match="对象键"):
        storage.exists(key)
    with pytest.raises(ValueError, match="对象键"):
        storage.delete(key)
    assert not (tmp_path.parent / "escape.txt").exists()


def test_storage_satisfies_protocol(tmp_path: Path) -> None:
    # 静态结构检查：LocalDirectoryStorage 实现了 ObjectStorage 端口
    storage: ObjectStorage = LocalDirectoryStorage(tmp_path)
    storage.put_bytes("k", b"v")
    assert storage.get_bytes("k") == b"v"
