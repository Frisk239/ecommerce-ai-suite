"""对象存储端口：上层（中台接口）依赖此协议，不依赖具体实现。"""

from typing import Protocol


class ObjectStorage(Protocol):
    """字节级对象存储的最小接口。

    key 是 posix 风格对象键（如 ``assets/2026/policy.md``），
    不是文件系统路径：实现方负责键的安全规范化。
    """

    def put_bytes(self, key: str, data: bytes) -> None:
        """写入一份字节。同键覆盖由调用方约束（资产键不复用），端口不承诺。"""
        ...

    def get_bytes(self, key: str) -> bytes:
        """读取一份字节；不存在时抛 FileNotFoundError。"""
        ...

    def exists(self, key: str) -> bool:
        """该键是否已存在。"""
        ...

    def delete(self, key: str) -> None:
        """删除该键；不存在时静默（幂等）。"""
        ...
