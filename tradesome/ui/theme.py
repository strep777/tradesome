"""视觉令牌：颜色、字体、间距、圆角。界面所有颜色都从这里取。

改主题只需要改这个文件里的两组色板，其它页面不用动。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

MACOS = {
    "bg": "#F2F2F7", "surface": "#FFFFFF", "surface_alt": "#E9E9EE",
    "border": "#D8D8DE", "text": "#1D1D1F", "muted": "#6E6E73",
    "faint": "#A1A1A6", "accent": "#007AFF", "accent_hover": "#0066D6",
    "accent_soft": "#E5F0FF", "up": "#FF3B30", "down": "#34C759",
    "warning": "#FF9F0A", "danger": "#FF3B30", "success": "#34C759",
    "chart_grid": "#E5E5EA", "ma5": "#FF9F0A", "ma20": "#007AFF",
    "boll": "#AF52DE", "selection": "#D6E4FF", "shadow": "#D8D8DE",
    "danger_hover": "#E0342A", "success_hover": "#28A745",
    "warning_hover": "#E08E00", "hover": "#F4F4F8",
}

DARK = {
    "bg": "#0F1115", "surface": "#171A21", "surface_alt": "#1E232C",
    "border": "#2A303B", "text": "#E6E8EC", "muted": "#9AA3B2",
    "faint": "#6B7482", "accent": "#4C8DFF", "accent_hover": "#6AA1FF",
    "accent_soft": "#1B2A44", "up": "#F04452", "down": "#22C55E",
    "warning": "#F5A524", "danger": "#E5484D", "success": "#22C55E",
    "chart_grid": "#232936", "ma5": "#F5A524", "ma20": "#4C8DFF",
    "boll": "#A78BFA", "selection": "#24334C", "shadow": "#0B0D11",
    "danger_hover": "#D13B40", "success_hover": "#1EA84F",
    "warning_hover": "#DE941F", "hover": "#232A35",
}

LIGHT = {
    "bg": "#F6F7F9", "surface": "#FFFFFF", "surface_alt": "#F1F3F6",
    "border": "#E3E7ED", "text": "#1B1F26", "muted": "#5F6B7A",
    "faint": "#8A93A0", "accent": "#2F6BFF", "accent_hover": "#1E56E0",
    "accent_soft": "#E8EEFF", "up": "#D92D3A", "down": "#15803D",
    "warning": "#B45309", "danger": "#C62828", "success": "#15803D",
    "chart_grid": "#E6EAF0", "ma5": "#B45309", "ma20": "#2F6BFF",
    "boll": "#7C3AED", "selection": "#DCE7FF", "shadow": "#D9DEE6",
    "danger_hover": "#B71C1C", "success_hover": "#116C34",
    "warning_hover": "#96460A", "hover": "#EAEDF2",
}

_THEMES = {"macos": MACOS, "dark": DARK, "light": LIGHT}

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"card": 12, "button": 8, "input": 8}
FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"

FONT_ROLES = {
    "h1": (20, "bold"), "h2": (13, "bold"), "body": (10, "normal"),
    "caption": (9, "normal"), "table": (10, "normal"), "kpi": (24, "bold"),
    "mono": (10, "normal"), "small": (8, "normal"), "button": (10, "normal"),
}

_state = {"theme": "macos", "scale": 1.0, "dpi": 1.0, "palette": MACOS}


def set_theme(name: str = "macos", scale: float = 1.0) -> None:
    name = name if name in _THEMES else "macos"
    _state["theme"] = name
    _state["palette"] = _THEMES[name]
    _state["scale"] = max(0.8, min(1.5, float(scale or 1.0)))


def set_dpi(factor: float = 1.0) -> None:
    """记录系统 DPI 因子（120 DPI → 1.25）。

    字体用 pt，由 Tk 的 ``scaling`` 自动换算成物理像素，因此这里只影响
    由 ``px()`` 表达的布局尺寸，避免二次放大。
    """
    try:
        value = float(factor)
    except (TypeError, ValueError):
        value = 1.0
    _state["dpi"] = max(0.75, min(3.0, value))


def dpi() -> float:
    return float(_state["dpi"])


def theme_name() -> str:
    return _state["theme"]


def palette() -> dict:
    return _state["palette"]


def color(key: str, fallback: str = "#FF00FF") -> str:
    return _state["palette"].get(key, fallback)


def font(role: str = "body"):
    size, weight = FONT_ROLES.get(role, FONT_ROLES["body"])
    family = MONO_FAMILY if role == "mono" else FONT_FAMILY
    scaled = max(7, int(round(size * _state["scale"])))
    return (family, scaled, weight)


def px(value: int) -> int:
    """设计像素 → 物理像素：叠加用户字号缩放与系统 DPI 因子。"""
    return int(round(value * _state["scale"] * _state["dpi"]))


def apply(root: tk.Misc) -> None:
    """把令牌写进 ttk 样式；tk 原生控件由各控件自己上色。"""
    p = palette()
    root.configure(bg=p["bg"])
    try:
        root.option_add("*Font", font("body"))
        root.option_add("*Background", p["bg"])
        root.option_add("*Foreground", p["text"])
        root.option_add("*selectBackground", p["selection"])
        root.option_add("*selectForeground", p["text"])
        root.option_add("*Entry.Background", p["surface"])
        root.option_add("*Entry.Foreground", p["text"])
        root.option_add("*Entry.insertBackground", p["text"])
        root.option_add("*Entry.borderWidth", 0)
        root.option_add("*Entry.highlightThickness", 1)
        root.option_add("*Entry.highlightBackground", p["border"])
        root.option_add("*Entry.highlightColor", p["accent"])
        root.option_add("*Scrollbar.background", p["surface_alt"])
        root.option_add("*Scrollbar.troughColor", p["bg"])
        root.option_add("*Scrollbar.activeBackground", p["border"])
        root.option_add("*Scrollbar.highlightThickness", 0)
        root.option_add("*Scrollbar.borderWidth", 0)
        root.option_add("*Scrollbar.width", px(10))
        root.option_add("*TCombobox*Listbox.background", p["surface_alt"])
        root.option_add("*TCombobox*Listbox.foreground", p["text"])
    except tk.TclError:
        pass
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Treeview", background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["text"], bordercolor=p["border"], borderwidth=0,
                    rowheight=px(30), font=font("table"))
    style.configure("Treeview.Heading", background=p["surface"],
                    foreground=p["muted"], borderwidth=0, relief="flat",
                    font=font("caption"), padding=(px(8), px(7)))
    style.map("Treeview.Heading", background=[("active", p["surface_alt"])])
    style.map("Treeview", background=[("selected", p["selection"])],
              foreground=[("selected", p["text"])])
    style.configure("TCombobox", fieldbackground=p["surface"],
                    background=p["surface"], foreground=p["text"],
                    arrowcolor=p["muted"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"], padding=px(4))
    style.map("TCombobox", fieldbackground=[("readonly", p["surface"])],
              foreground=[("readonly", p["text"])],
              selectbackground=[("readonly", p["surface"])],
              selectforeground=[("readonly", p["text"])])
    scrollbar_opts = dict(background=p["surface_alt"], troughcolor=p["bg"],
                          bordercolor=p["bg"], arrowcolor=p["muted"],
                          lightcolor=p["surface_alt"], darkcolor=p["surface_alt"],
                          relief="flat", arrowsize=px(9), width=px(10))
    for style_name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(style_name, **scrollbar_opts)
    style.map("Vertical.TScrollbar", background=[("active", p["border"])],
              arrowcolor=[("active", p["text"])],
              lightcolor=[("active", p["border"])],
              darkcolor=[("active", p["border"])])
    style.map("Horizontal.TScrollbar", background=[("active", p["border"])],
              arrowcolor=[("active", p["text"])])
    style.map("TScrollbar",
              background=[("active", p["border"]), ("pressed", p["border"])],
              arrowcolor=[("active", p["text"])],
              lightcolor=[("active", p["border"])],
              darkcolor=[("active", p["border"])])
    style.configure("Horizontal.TProgressbar", background=p["accent"],
                    troughcolor=p["surface_alt"], bordercolor=p["surface_alt"],
                    lightcolor=p["accent"], darkcolor=p["accent"])
    style.configure("TSeparator", background=p["border"])
    style.configure("TNotebook", background=p["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=p["surface_alt"], foreground=p["muted"],
                    padding=(px(12), px(6)), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", p["surface"])],
              foreground=[("selected", p["text"])])


def clear_font_cache() -> None:
    """字体缩放变化后，旧控件需要重建；这里只清缓存标记。"""
    _state["scale"] = max(0.8, min(1.5, float(_state["scale"])))
