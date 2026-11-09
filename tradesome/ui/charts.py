"""Canvas 图表：K 线、折线、迷你柱、火花线。

不依赖 matplotlib，颜色全部取自主题，跨平台一致。
"""

from __future__ import annotations

import math
import tkinter as tk
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from . import theme
from ..strategy.indicators import sma
from ..core.types import Bar

Padding = Tuple[int, int, int, int]


class _ChartBase(tk.Canvas):
    def __init__(self, parent, height: int = 220, bg: Optional[str] = None,
                 padding: Padding = (12, 12, 12, 26), **kwargs) -> None:
        super().__init__(parent, bg=bg or theme.color("surface"), bd=0,
                         highlightthickness=0, height=theme.px(height), **kwargs)
        self._pad = tuple(theme.px(v) for v in padding)
        self._redraw_job = None
        self.bind("<Configure>", self._schedule)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
            self._redraw_job = None

    def _schedule(self, _event=None) -> None:
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
        self._redraw_job = self.after(60, self._redraw_safe)

    def _redraw_safe(self) -> None:
        self._redraw_job = None
        try:
            self._draw()
        except tk.TclError:
            pass

    def _draw(self) -> None:  # pragma: no cover - 子类实现
        self.delete("all")

    def _plot_box(self, height: Optional[int] = None):
        width = max(1, self.winfo_width())
        height = height or max(1, self.winfo_height())
        left, top, right, bottom = self._pad
        return left, top, width - right, height - bottom

    def _empty(self, message: str = "暂无数据") -> None:
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.create_text(width // 2, height // 2, text=message,
                         fill=theme.color("faint"), font=theme.font("caption"))


class LineChart(_ChartBase):
    """权益曲线：可叠加基准线，或显示回撤面积。"""

    def __init__(self, parent, height: int = 200, fill: bool = True,
                 mode: str = "line", **kwargs) -> None:
        super().__init__(parent, height=height, padding=(12, 16, 58, 26), **kwargs)
        self.series: List[Tuple[datetime, float]] = []
        self.secondary: List[Tuple[datetime, float]] = []
        self.fill = fill
        self.mode = mode
        self._bind_hover()

    def set_data(self, series: Sequence[Tuple[datetime, float]],
                 secondary: Optional[Sequence[Tuple[datetime, float]]] = None,
                 mode: Optional[str] = None) -> None:
        self.series = list(series or [])
        self.secondary = list(secondary or [])
        if mode:
            self.mode = mode
        self._schedule()

    def _bind_hover(self) -> None:
        self.tag_bind("hover_area", "<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def _on_motion(self, event) -> None:
        if not self.series:
            return
        left, top, right, bottom = self._plot_box()
        if not (left <= event.x <= right):
            return
        n = len(self.series)
        index = int(round((event.x - left) / max(1, right - left) * (n - 1)))
        index = max(0, min(n - 1, index))
        moment, value = self.series[index]
        self.delete("hover")
        self.create_line(event.x, top, event.x, bottom,
                         fill=theme.color("border"), tags="hover")
        text = "%s  %s" % (moment.strftime("%Y-%m-%d"), _fmt(value))
        box = self.create_text(event.x + 10, top + 10, text=text, anchor="nw",
                               fill=theme.color("text"), font=theme.font("small"),
                               tags="hover")
        bbox = self.bbox(box)
        if bbox:
            rect = self.create_rectangle(bbox[0] - 6, bbox[1] - 3,
                                         bbox[2] + 6, bbox[3] + 3,
                                         fill=theme.color("surface_alt"),
                                         outline=theme.color("border"), tags="hover")
            self.tag_lower(rect, box)
        self.tag_raise("hover")

    def _draw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._plot_box()
        width = right - left
        height = bottom - top
        if width <= 20 or height <= 20:
            return
        if not self.series:
            self._empty()
            return
        values = [v for _, v in self.series]
        second_values = [v for _, v in self.secondary]
        low = min(values + second_values) if second_values else min(values)
        high = max(values + second_values) if second_values else max(values)
        if high - low < 1e-9:
            high = low + 1.0
        pad = (high - low) * 0.08
        low -= pad
        high += pad
        span = high - low
        base_y = bottom - (0 - low) / span * height if self.mode == "drawdown" else bottom

        for step in range(5):
            ratio = step / 4.0
            y = top + ratio * height
            value = high - ratio * span
            self.create_line(left, y, right, y, fill=theme.color("chart_grid"),
                             dash=(1, 4) if step else ())
            self.create_text(right + 8, y, text=_fmt(value), anchor="w",
                             fill=theme.color("faint"), font=theme.font("small"))

        def point(index: int, value: float) -> Tuple[float, float]:
            x = left + (width * index / max(1, len(self.series) - 1))
            y = bottom - (value - low) / span * height
            return x, y

        points = [point(i, v) for i, (_, v) in enumerate(self.series)]
        if self.fill and len(points) > 1:
            fill_base = base_y
            polygon = [p for pair in points for p in pair]
            polygon += [right, fill_base, left, fill_base]
            color = theme.color("accent_soft")
            self.create_polygon(polygon, fill=color, outline="", stipple="")
        if self.mode == "drawdown":
            if len(points) > 1:
                line = [p for pair in points for p in pair]
                line += [right, bottom, left, bottom]
                self.create_polygon(line, fill=theme.color("accent_soft"),
                                    outline="")
            self.create_line([p for pair in points for p in pair],
                             fill=theme.color("up"), width=2, smooth=False)
            self.create_line(left, base_y, right, base_y,
                             fill=theme.color("border"), dash=(2, 3))
        else:
            self.create_line([p for pair in points for p in pair],
                             fill=theme.color("accent"), width=2, smooth=False)
            if len(points) > 1:
                last_x, last_y = points[-1]
                self.create_oval(last_x - 3, last_y - 3, last_x + 3, last_y + 3,
                                 fill=theme.color("accent"), outline="")
        if self.secondary:
            second_points = [point(i, v) for i, (_, v) in enumerate(self.secondary)]
            self.create_line([p for pair in second_points for p in pair],
                             fill=theme.color("ma5"), width=1, dash=(4, 3))
        self._x_labels(left, right, bottom)
        self.create_rectangle(left, top, right, bottom, outline="",
                              tags="hover_area")

    def _x_labels(self, left: int, right: int, bottom: int) -> None:
        if not self.series:
            return
        n = len(self.series)
        count = 4
        for i in range(count):
            index = int((n - 1) * i / (count - 1))
            moment = self.series[index][0]
            x = left + (right - left) * index / max(1, n - 1)
            anchor = "w" if i == 0 else "e" if i == count - 1 else "center"
            self.create_text(x, bottom + 8, text=moment.strftime("%Y-%m"),
                             anchor="n" if i not in (0, count - 1) else
                             ("nw" if i == 0 else "ne"),
                             fill=theme.color("faint"), font=theme.font("small"))


class CandlestickChart(_ChartBase):
    def __init__(self, parent, height: int = 330, **kwargs) -> None:
        super().__init__(parent, height=height, padding=(12, 12, 58, 26), **kwargs)
        self.bars: List[Bar] = []
        self.ma_periods = (5, 20)
        self._ma_lines: List[List[float]] = []
        self.show_volume = True
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self.delete("crosshair"))

    def set_bars(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars or [])
        closes = [b.close for b in self.bars]
        self._ma_lines = [sma(closes, period) for period in self.ma_periods]
        self._schedule()

    def _draw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._plot_box()
        width = max(1, right - left)
        if not self.bars or width < 20:
            self._empty("选择左侧标的查看 K 线")
            return
        price_bottom = bottom if not self.show_volume else bottom - max(34, (bottom - top) * 0.18)
        volume_top = price_bottom + 12
        n = len(self.bars)
        lows = [b.low for b in self.bars]
        highs = [b.high for b in self.bars]
        low, high = min(lows), max(highs)
        if high - low < 1e-6:
            high = low + 1.0
        pad = (high - low) * 0.06
        low -= pad
        high += pad
        span = high - low
        height = price_bottom - top

        for step in range(5):
            ratio = step / 4.0
            y = top + ratio * height
            value = high - ratio * span
            self.create_line(left, y, right, y, fill=theme.color("chart_grid"),
                             dash=(1, 4) if step else ())
            self.create_text(right + 8, y, text="%.2f" % value, anchor="w",
                             fill=theme.color("faint"), font=theme.font("small"))
        self.create_rectangle(left, top, right, price_bottom, outline="")

        slot = width / n
        candle = max(1.5, min(9.0, slot * 0.62))
        max_volume = max((b.volume for b in self.bars), default=0) or 1.0

        def y_of(price: float) -> float:
            return price_bottom - (price - low) / span * height

        for index, bar in enumerate(self.bars):
            x = left + slot * (index + 0.5)
            color = theme.color("up") if bar.close >= bar.open else theme.color("down")
            self.create_line(x, y_of(bar.high), x, y_of(bar.low), fill=color, width=1)
            top_body = y_of(max(bar.open, bar.close))
            bottom_body = y_of(min(bar.open, bar.close))
            if bottom_body - top_body < 1:
                bottom_body = top_body + 1
            if bar.close >= bar.open:
                self.create_rectangle(x - candle / 2, top_body, x + candle / 2,
                                      bottom_body, fill=color, outline=color)
            else:
                self.create_rectangle(x - candle / 2, top_body, x + candle / 2,
                                      bottom_body, fill=color, outline=color)
            if self.show_volume:
                volume_height = (bar.volume / max_volume) * (bottom - volume_top)
                self.create_rectangle(x - candle / 2, bottom - volume_height,
                                      x + candle / 2, bottom,
                                      fill=theme.color("surface_alt"), outline="")

        for positions, period, key in zip(self._ma_lines, self.ma_periods,
                                          ("ma5", "ma20")):
            points: List[float] = []
            for index, value in enumerate(positions):
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    continue
                x = left + slot * (index + 0.5)
                points.extend([x, y_of(value)])
            if len(points) >= 4:
                self.create_line(points, fill=theme.color(key), width=1.2)
        self._legend(left, top, right)
        if self.show_volume:
            self.create_line(left, volume_top - 6, right, volume_top - 6,
                             fill=theme.color("chart_grid"), dash=(2, 4))
        for i in range(4):
            index = int((n - 1) * i / 3)
            bar = self.bars[index]
            x = left + slot * (index + 0.5)
            anchor = "nw" if i == 0 else "ne" if i == 3 else "n"
            self.create_text(x, bottom + 8, text=bar.dt.strftime("%Y-%m-%d"),
                             anchor=anchor, fill=theme.color("faint"),
                             font=theme.font("small"))

    def _legend(self, left: int, top: int, right: int) -> None:
        x = right - 4
        for period, key in zip(reversed(self.ma_periods), ("ma20", "ma5")):
            text = "MA%d" % period
            item = self.create_text(x, top + 4, text=text, anchor="ne",
                                    fill=theme.color(key), font=theme.font("small"))
            bbox = self.bbox(item)
            if bbox:
                x = bbox[0] - 10

    def _on_motion(self, event) -> None:
        if not self.bars:
            return
        left, top, right, bottom = self._plot_box()
        if not (left <= event.x <= right):
            self.delete("crosshair")
            return
        n = len(self.bars)
        slot = (right - left) / n
        index = max(0, min(n - 1, int((event.x - left) / max(slot, 1e-6))))
        bar = self.bars[index]
        self.delete("crosshair")
        x = left + slot * (index + 0.5)
        self.create_line(x, top, x, bottom, fill=theme.color("border"),
                         dash=(2, 3), tags="crosshair")
        lines = [
            "%s" % bar.dt.strftime("%Y-%m-%d"),
            "开 %.2f   高 %.2f" % (bar.open, bar.high),
            "低 %.2f   收 %.2f" % (bar.low, bar.close),
            "量 %s" % _volume_text(bar.volume),
        ]
        anchor = "nw" if x < (left + right) / 2 else "ne"
        offset = 12 if anchor == "nw" else -12
        text = self.create_text(x + offset, top + 8, text="\n".join(lines),
                                anchor=anchor, justify="left",
                                fill=theme.color("text"), font=theme.font("small"),
                                tags="crosshair")
        bbox = self.bbox(text)
        if bbox:
            rect = self.create_rectangle(bbox[0] - 8, bbox[1] - 6,
                                         bbox[2] + 8, bbox[3] + 6,
                                         fill=theme.color("surface_alt"),
                                         outline=theme.color("border"),
                                         tags="crosshair")
            self.tag_lower(rect, text)


class Sparkline(_ChartBase):
    def __init__(self, parent, height: int = 40, color: Optional[str] = None,
                 **kwargs) -> None:
        super().__init__(parent, height=height, padding=(2, 2, 2, 2), **kwargs)
        self.values: List[float] = []
        self.line_color = color or theme.color("accent")

    def set_values(self, values: Sequence[float]) -> None:
        self.values = [float(v) for v in values]
        self._schedule()

    def _draw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._plot_box()
        if len(self.values) < 2 or right - left < 4:
            return
        low, high = min(self.values), max(self.values)
        if high - low < 1e-9:
            high = low + 1.0
        points = []
        for index, value in enumerate(self.values):
            x = left + (right - left) * index / (len(self.values) - 1)
            y = bottom - (value - low) / (high - low) * (bottom - top)
            points.extend([x, y])
        self.create_line(points, fill=self.line_color, width=1.5)


class MiniBarChart(_ChartBase):
    def __init__(self, parent, height: int = 160, **kwargs) -> None:
        super().__init__(parent, height=height, padding=(8, 16, 8, 24), **kwargs)
        self.values: List[Tuple[str, float]] = []

    def set_values(self, values: Sequence[Tuple[str, float]]) -> None:
        self.values = list(values or [])
        self._schedule()

    def _draw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._plot_box()
        if not self.values:
            self._empty()
            return
        values = [v for _, v in self.values]
        low, high = min(0.0, min(values)), max(0.0, max(values))
        if high - low < 1e-9:
            high = low + 1.0
        span = high - low
        zero_y = bottom - (0 - low) / span * (bottom - top)
        self.create_line(left, zero_y, right, zero_y,
                         fill=theme.color("border"))
        slot = (right - left) / len(self.values)
        for index, (label, value) in enumerate(self.values):
            x = left + slot * (index + 0.5)
            y = bottom - (value - low) / span * (bottom - top)
            color = theme.color("up") if value >= 0 else theme.color("down")
            bar_width = max(3, slot * 0.5)
            self.create_rectangle(x - bar_width / 2, min(y, zero_y),
                                  x + bar_width / 2, max(y, zero_y),
                                  fill=color, outline="")
            self.create_text(x, bottom + 6, text=label, anchor="n",
                             fill=theme.color("faint"), font=theme.font("small"))

    def _draw_labels(self) -> None:
        pass


def _fmt(value: float) -> str:
    if abs(value) >= 1e8:
        return "%.2f亿" % (value / 1e8)
    if abs(value) >= 1e4:
        return "%.2f万" % (value / 1e4)
    return "%.2f" % value


def _volume_text(value: float) -> str:
    if value >= 1e8:
        return "%.2f亿股" % (value / 1e8)
    if value >= 1e4:
        return "%.2f万股" % (value / 1e4)
    return "%.0f股" % value
