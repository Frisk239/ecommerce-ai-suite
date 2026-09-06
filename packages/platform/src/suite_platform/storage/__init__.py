"""对象存储抽象与实现。

领域约束（ADR 0003）：资产字节住对象存储，记录里只放对象键；
每一版资产一把键，不复用、不覆盖。
"""

from suite_platform.storage.local import LocalDirectoryStorage
from suite_platform.storage.protocols import ObjectStorage

__all__ = ["LocalDirectoryStorage", "ObjectStorage"]
