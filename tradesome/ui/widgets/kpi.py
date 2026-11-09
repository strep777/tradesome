"""KPI 数字卡：概览、回测、模拟盘共享。"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from .. import theme
from .shapes import rounded_rect


def _parent_bg(widget) -> str:
    try:
        return str(widget.cget("bg"))
    except tk.TclError:
        return theme.color("bg")


class KpiCard(tk.Frame):
    def __init__(self, parent, label: str, value: str = "—", delta: str = "",
                 hint: str = "", accent: Optional[str] = None) -> None:
        page_bg = _parent_bg(parent)
        super().__init__(parent, bg=page_bg, bd=0, highlightthickness=0)
        self._canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        inner = tk.Frame(self, bg=theme.color("surface"))
        inner.pack(fill="both", expand=True, padx=theme.px(16), pady=theme.px(14))
        tk.Label(inner, text=label, bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(anchor="w")
        self.value_label = tk.Label(inner, text=value, bg=theme.color("surface"),
                                    fg=theme.color("faint") if value == "—" else theme.color("text"),
                                    font=theme.font("kpi"), anchor="w")
        self.value_label.pack(anchor="w", pady=(theme.px(6), 0))
        self.delta_label = tk.Label(inner, text=delta, bg=theme.color("surface"),
                                    fg=theme.color("muted"),
                                    font=theme.font("caption"), anchor="w")
        self.delta_label.pack(anchor="w", pady=(theme.px(2), 0))
        if hint:
            tk.Label(inner, text=hint, bg=theme.color("surface"),
                     fg=theme.color("faint"), font=theme.font("small"),
                     anchor="w").pack(anchor="w", pady=(theme.px(2), 0))
        self._radius = theme.px(theme.RADIUS["card"])
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None) -> None:
        width = event.width if event is not None else self.winfo_width()
        height = event.height if event is not None else self.winfo_height()
        if width < 4 or height < 4:
            return
        self._canvas.delete("card")
        rounded_rect(self._canvas, 1, 1, width - 1, height - 1, self._radius,
                     fill=theme.color("surface"), outline=theme.color("border"),
                     width=1, tags="card")
        self._canvas.tag_lower("card")

    def set(self, value: str, delta: str = "", tone: str = "muted",
            color: Optional[str] = None) -> None:
        if color is None and value == "—":
            color = theme.color("faint")
        self.value_label.configure(text=value, fg=color or theme.color(
            tone if tone in ("up", "down", "warning", "success") else "text"))
        self.delta_label.configure(text=delta,
                                   fg=theme.color(tone if tone != "muted" else "muted"))


class StatBlock(tk.Frame):
    """竖向小指标：标签在上、数值在下，用于详情栏。"""

    def __init__(self, parent, label: str, value: str = "—", width: int = 0,
                 bg: Optional[str] = None) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background)
        tk.Label(self, text=label, bg=background, fg=theme.color("muted"),
                 font=theme.font("small"), anchor="w").pack(anchor="w")
        self.value_label = tk.Label(self, text=value, bg=background,
                                    fg=theme.color("text"), font=theme.font("body"),
                                    anchor="w", width=width)
        self.value_label.pack(anchor="w", pady=(2, 0))

    def set(self, value: str, color: Optional[str] = None) -> None:
        self.value_label.configure(text=value,
                                   fg=color or theme.color("text"))
