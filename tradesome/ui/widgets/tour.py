"""新手引导：欢迎框、跨页高亮引导、页内步骤弹窗。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, List, Optional, Sequence

from .. import theme
from .button import Button
from .modal import Modal

ONBOARDING_KEY = "onboarding.done"

TOUR_STEPS: List[Dict[str, str]] = [
    {"page": "dashboard", "target": "steps", "title": "先看「三步上手」",
     "body": "概览页把顺序写好了：准备数据 → 跑一次回测 → 开模拟盘。右边的卡片可以直接点。"},
    {"page": "data", "target": "generate", "title": "第一步：准备数据",
     "body": "点「同步真实行情」抓一份真实日线（需安装 akshare）；也可以导入自己的 CSV，文件名用代码命名。"},
    {"page": "strategies", "target": "new", "title": "第二步：建一条自己的策略",
     "body": "点「新建策略」。可以选模板改参数，也可以切到「自由组合」，从零搭入场和退出条件。"},
    {"page": "backtest", "target": "start", "title": "第三步：跑回测",
     "body": "选策略和标的，点「开始回测」。先看最大回撤，再看收益；不满意就回策略库或实验室调。"},
    {"page": "live", "target": "start", "title": "第四步：模拟盘",
     "body": "模拟盘按真实节奏回放行情，下单、拒单、T+1 与风控都和实盘一致，但不碰真钱。"},
    {"page": "live_trade", "target": "gate", "title": "（可选）实盘",
     "body": "实盘默认关闭。先在设置里启用，再输入确认词解锁；本机没有 miniQMT 时先用「模拟柜台」。"},
    {"page": "help", "target": "tutorial", "title": "随时回来查",
     "body": "每个页面右上角都有「本页怎么用」；帮助页的教程中心和文档目录随时可查。"},
]

_PAGE_LABELS = {"dashboard": "概览", "data": "数据中心", "strategies": "策略库",
                "backtest": "回测", "live": "模拟盘", "live_trade": "实盘",
                "help": "帮助"}


class StepsModal(Modal):
    """页内「本页怎么用」：编号步骤 + 可选的整段引导入口。"""

    def __init__(self, parent, title: str, steps: Sequence[str],
                 on_start: Optional[Callable[[], None]] = None) -> None:
        height = 250 + min(len(steps), 6) * 34
        super().__init__(parent, title, width=560, height=height)
        tk.Label(self.body, text=title, bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("h2"),
                 anchor="w").pack(fill="x")
        tk.Label(self.body, text="按下面的顺序操作，不需要记快捷键。",
                 bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(fill="x", pady=(2, theme.px(10)))
        for index, text in enumerate(steps, start=1):
            row = tk.Frame(self.body, bg=theme.color("surface"))
            row.pack(fill="x", pady=(0, theme.px(7)))
            tk.Label(row, text=str(index), bg=theme.color("accent_soft"),
                     fg=theme.color("accent"), font=theme.font("caption"),
                     width=3).pack(side="left", anchor="n")
            tk.Label(row, text=text, bg=theme.color("surface"),
                     fg=theme.color("text"), font=theme.font("body"),
                     anchor="w", justify="left",
                     wraplength=theme.px(420)).pack(side="left", fill="x",
                                                   expand=True, padx=(theme.px(8), 0))
        if on_start is not None:
            Button(self.footer, "开始本页引导", variant="secondary",
                   command=lambda: (self.close(), on_start())).pack(side="left")
        Button(self.footer, "知道了", variant="primary",
               command=self.close).pack(side="right")


class WelcomeModal(Modal):
    def __init__(self, parent, on_start: Callable[[], None],
                 on_skip: Callable[[], None]) -> None:
        super().__init__(parent, "欢迎使用 TradeSome", width=560, height=360)
        tk.Label(self.body, text="第一次用？先花 3 分钟走一遍。",
                 bg=theme.color("surface"), fg=theme.color("text"),
                 font=theme.font("h1"), anchor="w").pack(fill="x")
        tk.Label(self.body, text=(
            "你会依次看到：数据、策略、回测、模拟盘和可选的实盘。"
            "每一步只高亮一个按钮，照着点就行；随时可以跳过，之后在帮助页重看。"),
                 bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("body"), anchor="w", justify="left",
                 wraplength=theme.px(420)).pack(fill="x", pady=(theme.px(10), theme.px(14)))
        for line in ("① 同步一份真实行情", "② 新建一条策略（模板或自由组合）",
                     "③ 跑回测，再看模拟盘", "④ 需要时再了解实盘"):
            tk.Label(self.body, text=line, bg=theme.color("surface"),
                     fg=theme.color("text"), font=theme.font("body"),
                     anchor="w").pack(fill="x", pady=(0, theme.px(4)))
        Button(self.footer, "开始 3 分钟引导", variant="primary",
               command=lambda: (self.close(), on_start())).pack(side="right")
        Button(self.footer, "先自己看看", variant="secondary",
               command=lambda: (self.close(), on_skip())).pack(
                   side="right", padx=(0, theme.px(8)))


class TourOverlay:
    """高亮目标控件并在旁边放一张说明卡。

    刻意不做整窗遮罩：Tk 无法让 Frame 半透明，全屏遮罩会把页面盖成一块灰。
    这里只画 2px 高亮边框 + 浮动卡片，页面内容保持可见，Esc 可随时退出。
    """

    CARD_WIDTH = 360

    def __init__(self, shell) -> None:
        self.shell = shell
        self._index = 0
        self._rect = None
        self._on_done: Optional[Callable[[], None]] = None
        self._card: Optional[tk.Frame] = None
        self._borders: List[tk.Frame] = []
        self._escape_id = None

    # ---------------------------------------------------------- 构建

    def _ensure_widgets(self) -> None:
        if self._card is not None:
            return
        surface = theme.color("surface")
        self._card = tk.Frame(self.shell, bg=surface,
                              highlightthickness=1,
                              highlightbackground=theme.color("border"))
        self.step_label = tk.Label(self._card, text="", bg=surface,
                                   fg=theme.color("muted"), font=theme.font("small"),
                                   anchor="w")
        self.step_label.pack(fill="x", padx=theme.px(14), pady=(theme.px(10), 0))
        self.title_label = tk.Label(self._card, text="", bg=surface,
                                    fg=theme.color("text"), font=theme.font("h2"),
                                    anchor="w", justify="left")
        self.title_label.pack(fill="x", padx=theme.px(14), pady=(theme.px(2), 0))
        self.body_label = tk.Label(self._card, text="", bg=surface,
                                   fg=theme.color("muted"), font=theme.font("body"),
                                   anchor="w", justify="left",
                                   wraplength=theme.px(320))
        self.body_label.pack(fill="x", padx=theme.px(14), pady=(theme.px(4), theme.px(10)))
        footer = tk.Frame(self._card, bg=surface)
        footer.pack(fill="x", padx=theme.px(12), pady=(0, theme.px(10)))
        Button(footer, "跳过", variant="ghost", size="sm",
               command=self.close).pack(side="left")
        self.next_button = Button(footer, "下一步", variant="primary", size="sm",
                                  command=self._next)
        self.next_button.pack(side="right")
        self.prev_button = Button(footer, "上一步", variant="secondary", size="sm",
                                  command=self._prev)
        self.prev_button.pack(side="right", padx=(0, theme.px(6)))
        accent = theme.color("accent")
        self._borders = [tk.Frame(self.shell, bg=accent) for _ in range(4)]

    def _destroy_widgets(self) -> None:
        for border in self._borders:
            try:
                border.destroy()
            except tk.TclError:
                pass
        self._borders = []
        if self._card is not None:
            try:
                self._card.destroy()
            except tk.TclError:
                pass
            self._card = None

    # ---------------------------------------------------------- 生命周期

    def start(self, index: int = 0,
              on_done: Optional[Callable[[], None]] = None) -> None:
        self.close(silent=True)
        self._ensure_widgets()
        self._on_done = on_done
        self._index = max(0, min(index, len(TOUR_STEPS) - 1))
        self._escape_id = self.shell.bind("<Escape>", self._on_escape, add="+")
        self.shell.after(80, self._render)

    def close(self, silent: bool = False) -> None:
        if self._escape_id:
            try:
                self.shell.unbind("<Escape>", self._escape_id)
            except tk.TclError:
                pass
            self._escape_id = None
        self._destroy_widgets()
        if not silent and self._on_done is not None:
            callback, self._on_done = self._on_done, None
            callback()

    def _on_escape(self, _event=None) -> None:
        self.close()

    # ---------------------------------------------------------- 步骤

    def _step(self) -> Dict[str, str]:
        return TOUR_STEPS[self._index]

    def _render(self) -> None:
        step = self._step()
        try:
            self.shell.navigate(step["page"])
        except Exception:  # noqa: BLE001 - 引导不应中断
            pass
        self.shell.update_idletasks()
        self._rect = self._resolve_target(step)
        self._place_highlight()
        self.step_label.configure(
            text="第 %d / %d 步 · %s" % (
                self._index + 1, len(TOUR_STEPS),
                _PAGE_LABELS.get(step["page"], step["page"])))
        self.title_label.configure(text=step["title"])
        self.body_label.configure(text=step["body"])
        self.prev_button.set_enabled(self._index > 0)
        self.next_button.set_text("完成" if self._index == len(TOUR_STEPS) - 1 else "下一步")
        self._place_card()

    def _resolve_target(self, step: Dict[str, str]):
        page = getattr(self.shell, "_pages", {}).get(step["page"])
        if page is None:
            return None
        try:
            targets = page.tour_targets()
        except Exception:  # noqa: BLE001
            return None
        widget = targets.get(step["target"]) if isinstance(targets, dict) else None
        if widget is None:
            return None
        try:
            x = widget.winfo_rootx() - self.shell.winfo_rootx()
            y = widget.winfo_rooty() - self.shell.winfo_rooty()
            return (x, y, widget.winfo_width(), widget.winfo_height())
        except tk.TclError:
            return None

    def _place_highlight(self) -> None:
        if not self._borders:
            return
        if self._rect is None:
            for border in self._borders:
                border.place_forget()
            return
        x, y, w, h = self._rect
        pad = theme.px(4)
        thick = theme.px(2)
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2, y2 = x + w + pad, y + h + pad
        specs = ((x1, y1, x2 - x1, thick), (x1, y2 - thick, x2 - x1, thick),
                 (x1, y1, thick, y2 - y1), (x2 - thick, y1, thick, y2 - y1))
        for border, (bx, by, bw, bh) in zip(self._borders, specs):
            border.place(x=bx, y=by, width=max(1, bw), height=max(1, bh))
            border.lift()

    def _place_card(self) -> None:
        if self._card is None:
            return
        self._card.update_idletasks()
        card_w = theme.px(self.CARD_WIDTH)
        card_h = max(theme.px(170), self._card.winfo_reqheight())
        width = max(1, self.shell.winfo_width())
        height = max(1, self.shell.winfo_height())
        margin = theme.px(16)
        rect = self._rect
        if rect is None:
            x = (width - card_w) // 2
            y = (height - card_h) // 2
        else:
            tx, ty, tw, th = rect
            x = tx + tw + margin
            if x + card_w > width - margin:
                x = tx - card_w - margin
            x = max(margin, min(x, width - card_w - margin))
            y = max(margin, min(ty, height - card_h - margin))
        self._card.place(x=x, y=y, width=card_w, height=card_h)
        self._card.lift()

    def _next(self) -> None:
        if self._index >= len(TOUR_STEPS) - 1:
            self.close()
            return
        self._index += 1
        self._render()

    def _prev(self) -> None:
        if self._index <= 0:
            return
        self._index -= 1
        self._render()
