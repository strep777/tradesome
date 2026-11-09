"""应用层：装配（bootstrap）、容器（container）、服务（services）。

依赖方向：ui → app → 领域层 → core。app 不导入 ui。
"""

from __future__ import annotations

__all__ = ["Paths", "Container", "build_container", "shutdown"]


def __getattr__(name: str):
    # 延迟导入，避免 `import tradesome.app` 时把 tkinter 相关模块拖进来
    if name == "Paths":
        from .paths import Paths
        return Paths
    if name == "Container":
        from .container import Container
        return Container
    if name == "build_container":
        from .bootstrap import build_container
        return build_container
    if name == "shutdown":
        from .bootstrap import shutdown
        return shutdown
    raise AttributeError(name)
