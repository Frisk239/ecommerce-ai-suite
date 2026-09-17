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


# ---------- 第 94b 刀：媒体端点用的 size / 区间流式读 ----------


def test_size_reports_bytes_and_missing_raises(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    storage.put_bytes("clips/a/b.mp4", b"0123456789")
    assert storage.size("clips/a/b.mp4") == 10
    with pytest.raises(FileNotFoundError):
        storage.size("clips/a/missing.mp4")


def test_iter_bytes_full_and_ranges(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    payload = bytes(range(256)) * 1000  # 256000 字节：跨多个 64KiB 分块
    storage.put_bytes("clips/a/b.mp4", payload)

    assert b"".join(storage.iter_bytes("clips/a/b.mp4")) == payload
    assert b"".join(storage.iter_bytes("clips/a/b.mp4", start=0, end=99)) == payload[:100]
    assert b"".join(storage.iter_bytes("clips/a/b.mp4", start=100)) == payload[100:]
    assert b"".join(storage.iter_bytes("clips/a/b.mp4", start=255900, end=255999)) == payload[-100:]
    # 分块（不是一次整读）：一块一 yield，跨分块的区间块数 > 1
    assert len(list(storage.iter_bytes("clips/a/b.mp4", start=0, end=200000))) > 1


def test_iter_bytes_rejects_bad_ranges_and_missing(tmp_path: Path) -> None:
    storage = LocalDirectoryStorage(tmp_path)
    storage.put_bytes("k.bin", b"0123456789")
    for kwargs in ({"start": -1}, {"start": 5, "end": 4}, {"start": 11}, {"start": 0, "end": 10}):
        with pytest.raises(ValueError):
            list(storage.iter_bytes("k.bin", **kwargs))
    # start == size 是空区间（合法）：零字节对象/空区间不抛，产出空
    assert list(storage.iter_bytes("k.bin", start=10)) == []
    with pytest.raises(FileNotFoundError):
        list(storage.iter_bytes("missing.bin"))
