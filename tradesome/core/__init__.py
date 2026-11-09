"""内核层：纯数据、纯函数、无 IO 的公共基础。

本包只能依赖标准库与自身，不得 import 其他 tradesome 子包。
"""

from __future__ import annotations

__all__ = [
    "types",
    "errors",
    "bus",
    "events",
    "clock",
    "config",
    "logging",
    "ids",
    "money",
    "instruments",
    "calendar",
]
