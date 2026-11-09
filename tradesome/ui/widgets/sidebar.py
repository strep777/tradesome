"""左侧导航栏：macOS 分组列表 + 蓝色圆角选中胶囊。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, List, Tuple

from .. import theme
from .shapes import rounded_rect

NavItem = Tuple[str, str, str]  # (key, glyph, label)


class Sidebar(tk.Frame):
    def __init__(self, parent, groups: List[Tuple[str, List[NavItem]]],
                 on_select: Callable[[str], None]) -> None:
        super().__init__(parent, bg=theme.color("surface_alt"),
                         width=theme.px(228))
        self.pack_propagate(False)
        self._on_select = on_select
        self._rows: Dict[str, dict] = {}
        self._active = ""
        self._hover = ""
        for title, items in groups:
            if title:
                tk.Label(self, text=title, bg=theme.color("surface_alt"),
                         fg=theme.color("faint"), font=theme.font("small"),
                         anchor="w").pack(fill="x", padx=theme.px(20),
                                          pady=(theme.px(16), theme.px(6)))
            for key, glyph, label in items:
                self._rows[key] = self._build_item(key, glyph, label)
        spacer = tk.Frame(self, bg=theme.color("surface_alt"))
        spacer.pack(fill="both", expand=True)
        tk.Label(self, text="模拟盘 · 本地运行", bg=theme.color("surface_alt"),
                 fg=theme.color("faint"), font=theme.font("small"),
                 anchor="w").pack(fill="x", padx=theme.px(20),
                                  pady=(0, theme.px(14)))

    def _build_item(self, key: str, glyph: str, label: str) -> dict:
        canvas = tk.Canvas(self, bg=theme.color("surface_alt"),
                           height=theme.px(34), highlightthickness=0, bd=0,
                           cursor="hand2")
        canvas.pack(fill="x", padx=theme.px(10), pady=theme.px(1))
        canvas.bind("<Configure>", lambda _e, k=key: self._draw(k))
        canvas.bind("<Button-1>", lambda _e, k=key: self._select(k))
        canvas.bind("<Enter>", lambda _e, k=key: self._enter(k))
        canvas.bind("<Leave>", lambda _e, k=key: self._leave(k))
        return {"canvas": canvas, "glyph": glyph, "label": label}

    def _draw(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        canvas: tk.Canvas = row["canvas"]
        width = max(canvas.winfo_width(), theme.px(160))
        height = theme.px(34)
        active = key == self._active
        hover = key == self._hover and not active
        canvas.delete("all")
        if active:
            fill = theme.color("accent")
            fg = "#FFFFFF"
        elif hover:
            fill = theme.color("surface")
            fg = theme.color("text")
        else:
            fill = theme.color("surface_alt")
            fg = theme.color("muted")
        rounded_rect(canvas, 1, 1, width - 1, height - 1, theme.px(7),
                     fill=fill, outline=fill)
        canvas.create_text(theme.px(16), height / 2, text=row["glyph"],
                           anchor="w", fill=fg,
                           font=(theme.FONT_FAMILY, 11))
        canvas.create_text(theme.px(40), height / 2, text=row["label"],
                           anchor="w", fill=fg, font=theme.font("body"))

    def _enter(self, key: str) -> None:
        self._hover = key
        self._draw(key)

    def _leave(self, key: str) -> None:
        if self._hover == key:
            self._hover = ""
        self._draw(key)

    def _select(self, key: str) -> None:
        self.set_active(key)
        self._on_select(key)

    def active_key(self) -> str:
        """当前选中的导航 key（测试与外部状态同步用）。"""
        return self._active

    def set_active(self, key: str) -> None:
        old = self._active
        self._active = key
        for item in {old, key}:
            if item:
                self._draw(item)
