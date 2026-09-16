"""本地目录版对象存储：开发与单机部署用，接口语义与远端实现保持一致。"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath

_EMPTY_ERROR = "对象键不能为空"
_SEP_ERROR = "对象键使用 posix 风格，不允许反斜杠: {key!r}"
_ESCAPE_ERROR = "对象键不得为绝对路径、盘符路径或包含 '..': {key!r}"
# 流式读的块大小（第 94b 刀媒体端点）：64KiB 对视频 Range 是常见的折中——
# 够大不至于把 Range 响应切得过碎，够小不至于把一格 206 变成整段进内存。
_CHUNK_SIZE = 64 * 1024


class LocalDirectoryStorage:
    """把一个目录当作对象存储桶。

    键安全：入参键按 posix 语义规范化，拒绝绝对路径、``..`` 段、反斜杠与
    Windows 盘符/UNC 前缀（跨平台一致）；解析后再做「结果必须落在 root 内」
    的二次校验，兜住符号链接等解析后逸出的情况。
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path_for(self, key: str) -> Path:
        if not key:
            raise ValueError(_EMPTY_ERROR)
        if "\\" in key:
            raise ValueError(_SEP_ERROR.format(key=key))
        pure = PurePosixPath(key)
        win = PureWindowsPath(key)
        if pure.is_absolute() or ".." in pure.parts or win.is_absolute() or win.drive != "":
            raise ValueError(_ESCAPE_ERROR.format(key=key))
        target = self._root.joinpath(*pure.parts)
        if not target.resolve().is_relative_to(self._root.resolve()):
            raise ValueError(_ESCAPE_ERROR.format(key=key))
        return target

    def put_bytes(self, key: str, data: bytes) -> None:
        target = self._path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        target = self._path_for(key)
        if not target.is_file():
            raise FileNotFoundError(f"对象不存在: {key}")
        return target.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def size(self, key: str) -> int:
        target = self._path_for(key)
        if not target.is_file():
            raise FileNotFoundError(f"对象不存在: {key}")
        return target.stat().st_size

    def iter_bytes(self, key: str, *, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """分块读 ``[start, end]``（闭区间）；end=None 读到文件末尾。

        用 ``seek`` + 读满一块即 yield：调用方（媒体端点）一次只持有一块，
        Range 响应不整读进内存（ADR 0052）。越界区间是调用方错误（ValueError；
        ``start == size`` 是空区间，合法）；416 的判定与文案在端点层，端口只
        负责「给的区间必须合法」。
        """
        if start < 0 or (end is not None and end < start):
            raise ValueError(f"非法字节区间: start={start} end={end}")
        target = self._path_for(key)
        if not target.is_file():
            raise FileNotFoundError(f"对象不存在: {key}")
        total = target.stat().st_size
        if start > total or (end is not None and end >= total):
            raise ValueError(f"字节区间越界: start={start} end={end} size={total}")
        remaining = (total if end is None else end + 1) - start
        with target.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(_CHUNK_SIZE, remaining))
                if not chunk:  # pragma: no cover - 文件在读到一半被截短
                    break
                remaining -= len(chunk)
                yield chunk
