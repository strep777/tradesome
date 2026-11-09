"""图形界面入口的兼容模块。

CLI 通过 ``from .ui.app import run`` 启动界面；真正的实现在 ``tradesome.ui``
里（延迟导入 ``Shell``，并在创建 Tk 前声明 DPI 感知）。保留本模块是为了
不破坏既有入口与文档中的调用路径。
"""

from __future__ import annotations

from . import enable_dpi_awareness, run

__all__ = ["run", "enable_dpi_awareness"]
