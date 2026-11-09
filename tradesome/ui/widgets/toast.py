"""轻提示：右上角浮层，3 秒自动消失，不打断操作。"""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional

from .. import theme

_KINDS = {"info": ("accent", 3200), "success": ("success", 3200),
          "warning": ("warning", 4500), "error": ("danger", 6000)}


class ToastManager:
    def __init__(self, host: tk.Misc) -> None:
        self.host = host
        self._stack = tk.Frame(host, bg=theme.color("bg"))
        self._items: List[tk.Frame] = []
        self._placed = False

    # 空栈必须从屏幕上撤下，否则会留下一个和最后一条提示一样大的色块。
    def _place(self) -> None:
        if not self._placed:
            self._stack.place(relx=1.0, x=-theme.px(20), y=theme.px(62),
                              anchor="ne")
            self._placed = True

    def _unplace(self) -> None:
        if self._placed:
            self._stack.place_forget()
            self._placed = False

    def show(self, message: str, kind: str = "info",
             duration: Optional[int] = None, title: str = "") -> None:
        accent, default_ms = _KINDS.get(kind, _KINDS["info"])
        frame = tk.Frame(self._stack, bg=theme.color("surface"),
                         highlightthickness=1,
                         highlightbackground=theme.color("border"))
        stripe = tk.Frame(frame, bg=theme.color(accent), width=theme.px(3))
        stripe.pack(side="left", fill="y")
        inner = tk.Frame(frame, bg=theme.color("surface"))
        inner.pack(side="left", fill="both", expand=True,
                   padx=theme.px(10), pady=theme.px(8))
        if title:
            tk.Label(inner, text=title, bg=theme.color("surface"),
                     fg=theme.color(accent), font=theme.font("caption"),
                     anchor="w").pack(anchor="w")
        label = tk.Label(inner, text=message, bg=theme.color("surface"),
                         fg=theme.color("text"), font=theme.font("caption"),
                         wraplength=theme.px(290), justify="left", anchor="w")
        label.pack(anchor="w")
        frame.pack(fill="x", pady=(0, theme.px(6)))
        self._items.append(frame)
        self._place()
        self._stack.lift()
        delay = duration if duration is not None else default_ms
        frame.after(delay, lambda: self._dismiss(frame))

    def _dismiss(self, frame: tk.Frame) -> None:
        if frame in self._items:
            self._items.remove(frame)
        try:
            frame.destroy()
        except tk.TclError:
            pass
        if not self._items:
            self._unplace()

    def clear(self) -> None:
        for frame in list(self._items):
            self._dismiss(frame)
        self._unplace()
