"""按钮：Canvas 圆角胶囊，统一外观与悬停反馈。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Optional

from .. import theme
from .shapes import rounded_rect

_VARIANTS = {
    "primary": ("accent", "accent_hover", "#FFFFFF", False),
    "secondary": ("surface_alt", "hover", "text", False),
    "ghost": ("__parent__", "accent_soft", "accent", False),
    "danger": ("danger", "danger_hover", "#FFFFFF", False),
    "success": ("success", "success_hover", "#FFFFFF", False),
    "warning": ("warning", "warning_hover", "#FFFFFF", False),
}


def _parent_bg(widget) -> str:
    try:
        return str(widget.cget("bg"))
    except tk.TclError:
        return theme.color("bg")


class Button(tk.Canvas):
    """圆角按钮。页面只依赖 ``set_enabled`` / ``set_text``，换实现不影响调用方。"""

    def __init__(self, parent, text: str, command: Optional[Callable] = None,
                 variant: str = "secondary", size: str = "md",
                 width: int = 0, bg: Optional[str] = None, **kwargs) -> None:
        base_key, hover_key, fg_key, _ = _VARIANTS.get(variant, _VARIANTS["secondary"])
        self._parent_bg = bg or _parent_bg(parent)
        background = self._parent_bg if base_key == "__parent__" else theme.color(base_key)
        self._normal = background
        self._hover = theme.color(hover_key)
        self._fg = theme.color(fg_key) if not fg_key.startswith("#") else fg_key
        self._variant = variant
        self._command = command
        self._enabled = True
        self._pressed = False
        self._focus = False
        self._text = text
        self._font = theme.font("button" if size == "md" else "caption")
        self._height = theme.px(30 if size == "md" else 26)
        self._pad_x = theme.px(16 if size == "md" else 11)
        self._radius = theme.px(8 if size == "md" else 7)
        self._fixed_width = theme.px(width) if width else 0
        width_px = self._measure()
        super().__init__(parent, width=width_px, height=self._height,
                         bg=self._parent_bg, highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2", **kwargs)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", lambda _e: self._fire())
        self.bind("<space>", lambda _e: self._fire())
        self._draw()

    # ---------- 尺寸与绘制 ----------
    def _apply_variant(self, variant: str) -> None:
        base_key, hover_key, fg_key, _ = _VARIANTS.get(variant, _VARIANTS["secondary"])
        self._variant = variant
        self._normal = (self._parent_bg if base_key == "__parent__"
                        else theme.color(base_key))
        self._hover = theme.color(hover_key)
        self._fg = fg_key if fg_key.startswith("#") else theme.color(fg_key)

    def set_variant(self, variant: str) -> None:
        """切换样式（如页签按钮的选中/未选中）。"""
        self._apply_variant(variant)
        self._draw()

    def _measure(self) -> int:
        if self._fixed_width:
            return self._fixed_width
        font = tkfont.Font(font=self._font)
        return font.measure(self._text) + 2 * self._pad_x

    def _draw(self) -> None:
        self.delete("all")
        width = max(int(self.winfo_width()), self._measure())
        height = int(self._height)
        if self._enabled:
            fill = self._hover if (self._pressed or self._focus) else self._normal
            fg = self._fg
        else:
            fill = theme.color("surface_alt")
            fg = theme.color("faint")
        outline = theme.color("border") if self._variant == "secondary" else fill
        rounded_rect(self, 1, 1, width - 1, height - 1,
                     self._radius, fill=fill, outline=outline, width=1)
        self.create_text(width / 2, height / 2, text=self._text,
                         fill=fg, font=self._font, anchor="center")
        if self._focus:
            rounded_rect(self, 1, 1, width - 1, height - 1, self._radius,
                         fill="", outline=theme.color("accent"), width=2)

    # ---------- 交互 ----------
    def _on_enter(self, _event=None) -> None:
        if self._enabled:
            self.configure(cursor="hand2")
            self._pressed = False
            self._draw()

    def _on_leave(self, _event=None) -> None:
        if self._enabled:
            self._pressed = False
            self._draw()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._draw()

    def _on_release(self, event=None) -> None:
        if not self._enabled or not self._pressed:
            return
        self._pressed = False
        self._draw()
        if event is not None:
            inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self._height
            if not inside:
                return
        self._fire()

    def _on_focus_in(self, _event=None) -> None:
        self._focus = True
        self._draw()

    def _on_focus_out(self, _event=None) -> None:
        self._focus = False
        self._draw()

    def _fire(self) -> None:
        if self._enabled and self._command is not None:
            self._command()

    # ---------- 公开方法 ----------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        try:
            self.configure(cursor="hand2" if enabled else "arrow",
                           takefocus=1 if enabled else 0)
        except tk.TclError:
            pass
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        if not self._fixed_width:
            self.configure(width=self._measure())
        self._draw()


class IconButton(Button):
    def __init__(self, parent, glyph: str, command=None, tooltip: str = "",
                 **kwargs) -> None:
        kwargs.setdefault("variant", "ghost")
        super().__init__(parent, glyph, command=command, **kwargs)
        self._tip = tooltip
