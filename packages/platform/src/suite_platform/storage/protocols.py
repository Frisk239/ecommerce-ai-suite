"""对象存储端口：上层（中台接口）依赖此协议，不依赖具体实现。"""

from collections.abc import Iterator
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

    def size(self, key: str) -> int:
        """对象字节数；不存在时抛 FileNotFoundError（第 94b 刀）。

        媒体端点的 Range 语义（Content-Length / Content-Range / 416 的
        ``bytes */size``）先要总长——不能靠整读来量（视频可达数百 MB）。
        """
        ...

    def iter_bytes(self, key: str, *, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """按区间**流式**读字节（第 94b 刀）：``[start, end]`` 闭区间，end=None
        读到末尾。分块实现，调用方一次只持有一块——视频 Range 响应不整读进内存。

        不存在时抛 FileNotFoundError；越界区间抛 ValueError（``start > size``、
        ``end >= size``、``end < start``）——合法性（含 416 判定）由媒体端点用
        ``size`` 先算后传。``start == size`` 是**空区间**（合法：零字节对象/空
        区间的通用表达），产出空迭代器，不抛。
        """
        ...
