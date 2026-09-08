"""本地目录版对象存储：开发与单机部署用，接口语义与远端实现保持一致。"""

from pathlib import Path, PurePosixPath, PureWindowsPath

_EMPTY_ERROR = "对象键不能为空"
_SEP_ERROR = "对象键使用 posix 风格，不允许反斜杠: {key!r}"
_ESCAPE_ERROR = "对象键不得为绝对路径、盘符路径或包含 '..': {key!r}"


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
