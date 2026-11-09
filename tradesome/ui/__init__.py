"""TradeSome 图形界面：Tkinter 单窗口 + 十一个页面。"""

from __future__ import annotations

import os
import sys

__all__ = ["run", "Shell", "enable_dpi_awareness"]


def enable_dpi_awareness() -> bool:
    """在创建 Tk 根窗口之前声明 DPI 感知。

    Windows 默认把未声明感知的进程当 96 DPI，125% 系统缩放下会整窗做位图拉伸，
    文字与 1px 边框因此发虚。这里按可用性依次尝试：

    1. ``SetProcessDpiAwarenessContext(-4)`` —— Per-Monitor V2（Win10 1703+，最佳）；
    2. ``shcore.SetProcessDpiAwareness(2)`` —— Per-Monitor；
    3. ``user32.SetProcessDPIAware()`` —— System DPI Aware（老系统兜底）。

    返回是否成功声明；非 Windows、缺少 API 或已经声明过都返回 False，调用方可忽略。
    该函数必须在 ``tk.Tk()`` 之前调用，否则无效。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
    except Exception:  # pragma: no cover - 极端环境
        return False

    # 1) Per-Monitor V2：-4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return True
    except Exception:  # pragma: no cover - 老系统没有该入口
        pass

    # 2) shcore：0= unaware, 1= system, 2= per-monitor
    try:
        shcore = ctypes.windll.shcore
        if hasattr(shcore, "SetProcessDpiAwareness"):
            if int(shcore.SetProcessDpiAwareness(2)) == 0:
                return True
    except Exception:  # pragma: no cover
        pass

    # 3) 老系统：System DPI Aware
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:  # pragma: no cover
        return False


def run(container) -> int:
    """启动界面并进入事件循环。返回进程退出码。"""
    enable_dpi_awareness()
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
    from .shell import Shell

    shell = Shell(container)
    shell.mainloop()
    return 0


def __getattr__(name):
    # 延迟导入，避免 cli --help 或服务层测试时加载 tkinter。
    if name == "Shell":
        from .shell import Shell

        return Shell
    raise AttributeError(name)
