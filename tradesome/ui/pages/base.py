"""页面基类：统一生命周期，页面只在第一次显示时构建。"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from .. import theme


class BasePage(tk.Frame):
    title = ""
    subtitle = ""
    key = ""
    # 页内的「本页怎么用」步骤；有内容时页头会出现按钮。
    help_steps = []

    def __init__(self, shell, container) -> None:
        super().__init__(shell.content, bg=theme.color("bg"))
        self.shell = shell
        self.c = container
        self._built = False

    # ---------------------------------------------------------- 生命周期

    def ensure_built(self) -> None:
        if self._built:
            return
        self._built = True
        self.build()

    def build(self) -> None:
        """子类在这里搭界面；只会调用一次。"""

    def on_show(self) -> None:
        self.ensure_built()
        self.refresh()

    def refresh(self) -> None:
        """页面每次显示时刷新数据。"""

    def on_hide(self) -> None:
        """页面被切走时调用，可停止动画等。"""

    def on_event(self, topic: str, payload: dict) -> None:
        """总线事件；默认忽略，需要的页面覆盖。"""

    # ---------------------------------------------------------- 小工具

    def page_header(self, title: Optional[str] = None,
                    subtitle: Optional[str] = None) -> tk.Frame:
        holder = tk.Frame(self, bg=theme.color("bg"))
        holder.pack(fill="x", padx=theme.px(24), pady=(theme.px(18), theme.px(6)))
        left = tk.Frame(holder, bg=theme.color("bg"))
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title or self.title, bg=theme.color("bg"),
                 fg=theme.color("text"), font=theme.font("h1"),
                 anchor="w").pack(anchor="w")
        text = subtitle if subtitle is not None else self.subtitle
        if text:
            tk.Label(left, text=text, bg=theme.color("bg"),
                     fg=theme.color("muted"), font=theme.font("caption"),
                     anchor="w", justify="left").pack(anchor="w", pady=(2, 0))
        if self.help_steps:
            from ..widgets.button import Button
            Button(holder, "本页怎么用", variant="ghost", size="sm",
                   command=lambda: self.shell.show_help_steps(self)).pack(
                       side="right", anchor="n", padx=(theme.px(8), 0))
        return holder

    def tour_targets(self) -> dict:
        """引导需要高亮的控件；默认没有。"""
        return {}

    def scroll_body(self) -> tk.Frame:
        from ..widgets.card import ScrollableFrame
        scroller = ScrollableFrame(self)
        scroller.pack(fill="both", expand=True)
        return scroller.body

    def toast(self, message: str, kind: str = "info") -> None:
        self.shell.toast(message, kind)

    def error(self, title: str, message: str, hint: str = "") -> None:
        self.shell.show_error(title, message, hint)
