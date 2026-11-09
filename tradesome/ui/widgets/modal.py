"""模态对话框：确认、错误提示。保持与主界面同一套配色。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from .. import theme
from .button import Button


class Modal(tk.Toplevel):
    def __init__(self, parent, title: str, width: int = 460,
                 height: int = 240) -> None:
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.title(title)
        self.configure(bg=theme.color("border"))
        border = tk.Frame(self, bg=theme.color("surface"))
        border.pack(fill="both", expand=True, padx=theme.px(1),
                    pady=theme.px(1))
        self.resizable(False, False)
        self.transient(parent)
        self._parent = parent
        self._width = theme.px(width)
        self._height = theme.px(height)
        header = tk.Frame(border, bg=theme.color("surface"))
        header.pack(fill="x", padx=theme.px(20), pady=(theme.px(16), 0))
        title_label = tk.Label(header, text=title, bg=theme.color("surface"),
                               fg=theme.color("text"), font=theme.font("h2"),
                               anchor="w")
        title_label.pack(side="left")
        self._drag_origin = None
        for widget in (header, title_label):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._stop_drag)
        close = tk.Label(header, text="✕", bg=theme.color("surface"),
                         fg=theme.color("muted"), font=(theme.FONT_FAMILY, 11),
                         cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        self.body = tk.Frame(border, bg=theme.color("surface"))
        self.body.pack(fill="both", expand=True, padx=theme.px(20),
                       pady=(theme.px(12), theme.px(8)))
        self.footer = tk.Frame(border, bg=theme.color("surface"))
        self.footer.pack(fill="x", padx=theme.px(20),
                         pady=(0, theme.px(16)))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _start_drag(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root,
                             self.winfo_x(), self.winfo_y())

    def _drag_move(self, event) -> None:
        if not self._drag_origin:
            return
        x0, y0, wx, wy = self._drag_origin
        self.geometry("+%d+%d" % (wx + event.x_root - x0,
                                  wy + event.y_root - y0))

    def _stop_drag(self, event) -> None:
        self._drag_origin = None

    def center(self) -> None:
        self.update_idletasks()
        try:
            root_x = self._parent.winfo_rootx()
            root_y = self._parent.winfo_rooty()
            root_w = self._parent.winfo_width()
            root_h = self._parent.winfo_height()
            x = root_x + (root_w - self._width) // 2
            y = root_y + (root_h - self._height) // 3
            self.geometry("%dx%d+%d+%d" % (self._width, self._height,
                                           max(0, x), max(0, y)))
        except tk.TclError:
            self.geometry("%dx%d" % (self._width, self._height))

    def show(self) -> None:
        self.center()
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class MessageModal(Modal):
    def __init__(self, parent, title: str, message: str, hint: str = "",
                 tone: str = "text") -> None:
        super().__init__(parent, title, width=480, height=250)
        tk.Label(self.body, text=message, bg=theme.color("surface"),
                 fg=theme.color(tone), font=theme.font("body"),
                 wraplength=theme.px(430), justify="left",
                 anchor="w").pack(fill="x")
        if hint:
            tk.Label(self.body, text=hint, bg=theme.color("surface"),
                     fg=theme.color("muted"), font=theme.font("caption"),
                     wraplength=theme.px(430), justify="left",
                     anchor="w").pack(fill="x", pady=(theme.px(10), 0))
        Button(self.footer, "知道了", command=self.close,
               variant="primary").pack(side="right")


class ConfirmModal(Modal):
    def __init__(self, parent, title: str, message: str,
                 on_confirm: Callable[[], None],
                 confirm_text: str = "确认", cancel_text: str = "取消",
                 danger: bool = False, require_phrase: str = "") -> None:
        super().__init__(parent, title, width=480, height=250 if not require_phrase else 300)
        self._on_confirm = on_confirm
        self._phrase = require_phrase
        tk.Label(self.body, text=message, bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("body"),
                 wraplength=theme.px(430), justify="left",
                 anchor="w").pack(fill="x")
        self._entry = None
        if require_phrase:
            tk.Label(self.body, text="请输入「%s」以确认：" % require_phrase,
                     bg=theme.color("surface"), fg=theme.color("muted"),
                     font=theme.font("caption"), anchor="w").pack(
                anchor="w", pady=(theme.px(12), theme.px(4)))
            box = tk.Frame(self.body, bg=theme.color("surface_alt"),
                           highlightthickness=1,
                           highlightbackground=theme.color("border"))
            box.pack(fill="x")
            entry = tk.Entry(box, bd=0, relief="flat", bg=theme.color("surface_alt"),
                             fg=theme.color("text"),
                             insertbackground=theme.color("text"),
                             font=theme.font("body"))
            entry.pack(fill="x", padx=theme.px(8), pady=theme.px(6))
            self._entry = entry
        Button(self.footer, cancel_text, command=self.close,
               variant="secondary").pack(side="right")
        Button(self.footer, confirm_text,
               command=self._confirm,
               variant="danger" if danger else "primary").pack(
            side="right", padx=(0, theme.px(8)))

    def _confirm(self) -> None:
        if self._entry is not None:
            if self._entry.get().strip() != self._phrase:
                self._entry.configure(bg=theme.color("accent_soft"))
                return
        self.close()
        self._on_confirm()
