"""行情页：报价列表 + K 线图 + 关键价位。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import theme
from ...data.resample import resample
from ..widgets import Button, Card, DataTable, SectionHeader, StatBlock
from .. import formatting as fmt
from .base import BasePage

TIMEFRAME_LABELS = [("日线", "1d"), ("周线", "1w"), ("月线", "1M")]


class MarketPage(BasePage):
    title = "行情"
    subtitle = "选择标的查看走势；周线、月线由日线聚合，不会产生未来数据。"

    help_steps = [
        '左侧列表选一个标的，右侧显示 K 线与成交量。',
        '周期可切日 / 周 / 月，聚合不会产生未来数据。',
        '没有数据时去数据中心「同步真实行情」或导入 CSV；也可以直接「用该标的回测」。',
    ]

    def tour_targets(self) -> dict:
        return {}

    def build(self) -> None:
        self._symbol = None
        self._bars = []
        header = self.page_header()
        self._build_toolbar(header)

        body = tk.Frame(self, bg=theme.color("bg"))
        body.pack(fill="both", expand=True, padx=theme.px(24), pady=(0, theme.px(16)))

        left = Card(body, padding=12)
        left.configure(width=theme.px(560))
        left.pack(side="left", fill="both")
        left.pack_propagate(False)
        self.table = DataTable(left.body, [
            ("symbol", "代码", 92), ("name", "名称", 96),
            ("price", "最新价", 78, "e"), ("change", "涨跌幅", 78, "e"),
            ("volume", "成交量", 78, "e"), ("state", "数据", 52, "center"),
        ], height=18, on_select=self._on_select, on_double=lambda r: self._use_for_backtest())
        self.table.pack(fill="both", expand=True)

        right = Card(body)
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))
        header_row = tk.Frame(right.body, bg=theme.color("surface"))
        header_row.pack(fill="x")
        self.name_label = tk.Label(header_row, text="选择一只标的",
                                   bg=theme.color("surface"),
                                   fg=theme.color("text"), font=theme.font("h2"),
                                   anchor="w")
        self.name_label.pack(side="left")
        self.price_label = tk.Label(header_row, text="—", bg=theme.color("surface"),
                                    fg=theme.color("text"), font=theme.font("kpi"))
        self.price_label.pack(side="right")
        self.change_label = tk.Label(header_row, text="", bg=theme.color("surface"),
                                     fg=theme.color("muted"), font=theme.font("caption"))
        self.change_label.pack(side="right", padx=(0, theme.px(10)))

        from ..charts import CandlestickChart
        self.chart = CandlestickChart(right.body, height=322)
        self.chart.pack(fill="both", expand=True, pady=(theme.px(10), theme.px(6)))

        stats_row = tk.Frame(right.body, bg=theme.color("surface"))
        stats_row.pack(fill="x", pady=(theme.px(4), 0))
        stats = tk.Frame(stats_row, bg=theme.color("surface"))
        stats.pack(side="left", fill="x", expand=True)
        self.stats = {}
        for key, label in (("open", "开盘"), ("high", "最高"), ("low", "最低"),
                           ("amount", "成交额"), ("range", "数据区间")):
            block = StatBlock(stats, label, "—")
            block.pack(side="left", padx=(0, theme.px(22)))
            self.stats[key] = block
        Button(stats_row, "用该标的回测", command=self._use_for_backtest,
               variant="secondary", size="sm").pack(side="right", padx=(theme.px(10), 0))

    def _build_toolbar(self, header) -> None:
        bar = tk.Frame(header, bg=theme.color("bg"))
        bar.pack(side="right")
        self.search_var = tk.StringVar()
        box = tk.Frame(bar, bg=theme.color("surface_alt"), highlightthickness=1,
                       highlightbackground=theme.color("border"))
        box.pack(side="left", padx=(0, theme.px(8)))
        tk.Label(box, text="搜索", bg=theme.color("surface_alt"),
                 fg=theme.color("faint"), font=theme.font("caption")).pack(
            side="left", padx=(theme.px(8), 0))
        entry = tk.Entry(box, textvariable=self.search_var, bd=0, relief="flat",
                         bg=theme.color("surface_alt"), fg=theme.color("text"),
                         insertbackground=theme.color("text"), font=theme.font("body"),
                         width=16)
        entry.pack(padx=(theme.px(5), theme.px(8)), pady=theme.px(5))
        entry.insert(0, "")
        self.search_var.trace_add("write", lambda *_: self._refresh_quotes())
        self.tf_var = tk.StringVar(value="日线")
        combo = ttk.Combobox(bar, textvariable=self.tf_var, state="readonly",
                             values=[label for label, _ in TIMEFRAME_LABELS],
                             width=6, font=theme.font("body"))
        combo.pack(side="left", padx=(0, theme.px(8)))
        combo.bind("<<ComboboxSelected>>", lambda e: self._load_chart())
        Button(bar, "刷新", command=self.refresh, variant="ghost", size="sm").pack(
            side="left", padx=(0, theme.px(6)))
        Button(bar, "同步真实行情", command=lambda: self.shell.navigate("data"),
               variant="secondary", size="sm").pack(side="left")

    # ---------------------------------------------------------- 数据

    def refresh(self) -> None:
        self._refresh_quotes()
        self._load_chart()

    def _refresh_quotes(self) -> None:
        keyword = (self.search_var.get() or "").strip().lower()
        rows = []
        for quote in self.c.services.market.quotes():
            if keyword and keyword not in quote["symbol"].lower() \
                    and keyword not in (quote["name"] or "").lower():
                continue
            rows.append({
                "symbol": quote["symbol"], "name": quote["name"],
                "price": fmt.money(quote["price"]) if quote["price"] else "—",
                "change": fmt.pct(quote["change_pct"], signed=True)
                if quote["has_data"] else "—",
                "volume": fmt.compact(quote["volume"]) if quote["has_data"] else "—",
                "state": "●" if quote["has_data"] else "○",
                "tone": quote["change_pct"] if quote["has_data"] else None,
            })
        self.table.set_rows_colored(rows, tone_key="tone")
        if self._symbol is None and rows:
            row = self.table.select_first()
            if row:
                self._symbol = row["symbol"]

    def _timeframe(self) -> str:
        return dict(TIMEFRAME_LABELS).get(self.tf_var.get(), "1d")

    def _on_select(self, row) -> None:
        if row and row.get("symbol"):
            self._symbol = row["symbol"]
            self._load_chart()

    def _load_chart(self) -> None:
        symbol = self._symbol
        if not symbol:
            return
        instrument = self.c.instruments.get(symbol)
        self.name_label.configure(text="%s  %s" % (symbol, instrument.name if instrument else ""))
        timeframe = self._timeframe()
        bars = self.c.services.market.history(symbol, "1d")
        if timeframe != "1d" and bars:
            bars = resample(bars, timeframe)
        bars = bars[-260:]
        self._bars = bars
        if not bars:
            self.chart.set_bars([])
            self.price_label.configure(text="—", fg=theme.color("muted"))
            self.change_label.configure(text="暂无数据，去数据中心同步或导入")
            for block in self.stats.values():
                block.set("—")
            return
        latest = bars[-1]
        previous = bars[-2] if len(bars) > 1 else None
        change = (latest.close / previous.close - 1.0) if previous else 0.0
        tone = "up" if change > 0 else "down" if change < 0 else "muted"
        self.price_label.configure(text=fmt.money(latest.close),
                                   fg=theme.color(tone))
        self.change_label.configure(text="%s  %s" % (fmt.pct(change, signed=True),
                                                    latest.dt.strftime("%Y-%m-%d")),
                                    fg=theme.color(tone))
        self.chart.set_bars(bars)
        self.stats["open"].set(fmt.money(latest.open))
        self.stats["high"].set(fmt.money(latest.high))
        self.stats["low"].set(fmt.money(latest.low))
        self.stats["amount"].set(fmt.compact(latest.amount))
        self.stats["range"].set("%s ~ %s" % (bars[0].dt.strftime("%Y-%m-%d"),
                                             bars[-1].dt.strftime("%Y-%m-%d")))

    def _use_for_backtest(self) -> None:
        if not self._symbol:
            self.toast("先选择一只标的", "warning")
            return
        self.shell.navigate("backtest")
        page = getattr(self.shell, "_pages", {}).get("backtest")
        if page is not None and hasattr(page, "preset_symbols"):
            page.preset_symbols([self._symbol])
