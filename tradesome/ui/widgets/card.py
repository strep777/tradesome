"""卡片、分区标题、空状态等基础容器。"""

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


class Card(tk.Frame):
    """白色圆角卡片：Canvas 画底，``body`` 放内容。"""

    def __init__(self, parent, padding: int = 16, radius: Optional[int] = None,
                 **kwargs) -> None:
        page_bg = _parent_bg(parent)
        kwargs.setdefault("bg", page_bg)
        super().__init__(parent, bd=0, highlightthickness=0, **kwargs)
        self.padding = padding
        self.radius = theme.px(radius if radius is not None else theme.RADIUS["card"])
        self._page_bg = page_bg
        self._canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=theme.color("surface"))
        self.body.pack(fill="both", expand=True,
                       padx=theme.px(padding), pady=theme.px(padding))
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None) -> None:
        width = event.width if event is not None else self.winfo_width()
        height = event.height if event is not None else self.winfo_height()
        if width < 4 or height < 4:
            return
        self._canvas.delete("card")
        rounded_rect(self._canvas, 1, 1, width - 1, height - 1, self.radius,
                     fill=theme.color("surface"), outline=theme.color("border"),
                     width=1, tags="card")
        self._canvas.tag_lower("card")


class SectionHeader(tk.Frame):
    def __init__(self, parent, title: str, subtitle: str = "",
                 bg: Optional[str] = None, **kwargs) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background, **kwargs)
        left = tk.Frame(self, bg=background)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title, bg=background, fg=theme.color("text"),
                 font=theme.font("h2"), anchor="w").pack(anchor="w")
        if subtitle:
            tk.Label(left, text=subtitle, bg=background, fg=theme.color("muted"),
                     font=theme.font("caption"), anchor="w",
                     justify="left").pack(anchor="w", pady=(3, 0))
        self.actions = tk.Frame(self, bg=background)
        self.actions.pack(side="right", anchor="e")


class Divider(tk.Frame):
    def __init__(self, parent, bg: Optional[str] = None, **kwargs) -> None:
        super().__init__(parent, height=max(1, theme.px(1)),
                         bg=bg or theme.color("border"), **kwargs)


class EmptyState(tk.Frame):
    def __init__(self, parent, title: str, message: str = "",
                 action_text: str = "", command=None, bg: Optional[str] = None) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background)
        box = tk.Frame(self, bg=background)
        box.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(box, text="◌", bg=background, fg=theme.color("faint"),
                 font=(theme.FONT_FAMILY, 28)).pack()
        tk.Label(box, text=title, bg=background, fg=theme.color("muted"),
                 font=theme.font("h2")).pack(pady=(6, 2))
        if message:
            tk.Label(box, text=message, bg=background, fg=theme.color("faint"),
                     font=theme.font("caption"), wraplength=theme.px(360),
                     justify="center").pack()
        if action_text and command is not None:
            from .button import Button
            Button(box, action_text, command=command, variant="primary",
                   size="sm").pack(pady=(12, 0))


class ScrollableFrame(tk.Frame):
    """竖向滚动容器：内部 body 放内容，滚轮只在鼠标进入时生效。"""

    def __init__(self, parent, bg: Optional[str] = None, **kwargs) -> None:
        background = bg or theme.color("bg")
        super().__init__(parent, bg=background, **kwargs)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, bd=0)
        from tkinter import ttk
        self.scroll = ttk.Scrollbar(self, orient="vertical",
                                    command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=background)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_body(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass
