"""行情页：报价列表 + K 线图 + 关键价位。"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import date, timedelta
from tkinter import ttk

from .. import theme
from ...data.resample import resample
from ..widgets import Button, Card, DataTable, SectionHeader, StatBlock
from .. import formatting as fmt
from .base import BasePage

TIMEFRAME_LABELS = [("日线", "1d"), ("周线", "1w"), ("月线", "1M")]
RANGE_LABELS = [("近 3 年", "3y"), ("近 1 年", "1y"),
                ("近 5 年", "5y"), ("全部历史", "all")]


class MarketPage(BasePage):
    title = "行情"
    subtitle = "选择标的查看走势；周线、月线由日线聚合，不会产生未来数据。"

    help_steps = [
        '左侧列表选一个标的，右侧显示 K 线与成交量；只有当日快照时会自动补齐历史日线。',
        '周期可切日 / 周 / 月，聚合不会产生未来数据。',
        '补齐失败可点图表右上「补齐历史」重试；也可以在数据中心批量同步或导入 CSV。',
    ]

    def tour_targets(self) -> dict:
        return {}

    def build(self) -> None:
        self._symbol = None
        self._changes = {}
        self._bars = []
        self._hist_loading = set()
        self._hist_failed = {}
        self._hist_result = {}
        self._suppress_select = False
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
        self.history_button = Button(header_row, "补齐历史", command=self._retry_history,
                                     variant="ghost", size="sm")
        self.history_button.pack(side="right", padx=(0, theme.px(8)))

        from ..charts import CandlestickChart
        self.chart = CandlestickChart(right.body, height=322)
        self.chart.pack(fill="both", expand=True, pady=(theme.px(10), theme.px(6)))

        Button(header_row, "重置", command=lambda: self.chart.reset_zoom(),
               variant="ghost", size="sm", width=44).pack(side="right",
                                                          padx=(0, theme.px(4)))
        Button(header_row, "－", command=lambda: self.chart.zoom_at(0.5, 1.25),
               variant="ghost", size="sm", width=28).pack(side="right",
                                                          padx=(0, theme.px(2)))
        Button(header_row, "＋", command=lambda: self.chart.zoom_at(0.5, 0.8),
               variant="ghost", size="sm", width=28).pack(side="right",
                                                          padx=(0, theme.px(2)))

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
        self.range_var = tk.StringVar(value="近 3 年")
        range_combo = ttk.Combobox(bar, textvariable=self.range_var, state="readonly",
                                   values=[label for label, _ in RANGE_LABELS],
                                   width=9, font=theme.font("body"))
        range_combo.pack(side="left", padx=(0, theme.px(8)))
        range_combo.bind("<<ComboboxSelected>>", lambda e: self._load_chart())
        Button(bar, "刷新", command=self.refresh, variant="ghost", size="sm").pack(
            side="left", padx=(0, theme.px(6)))
        Button(bar, "同步真实行情", command=lambda: self.shell.navigate("data"),
               variant="secondary", size="sm").pack(side="left")

    # ---------------------------------------------------------- 数据

    def refresh(self) -> None:
        self._refresh_quotes()
        self._load_chart()
        self._ensure_history()

    def _refresh_quotes(self) -> None:
        keyword = (self.search_var.get() or "").strip().lower()
        self._changes = {}
        rows = []
        for quote in self.c.services.market.quotes():
            if quote["has_data"]:
                self._changes[quote["symbol"]] = quote["change_pct"]
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
        self._suppress_select = True
        try:
            if self._symbol:
                self.table.select_value("symbol", self._symbol)
            if self._symbol is None and rows:
                row = self.table.select_first()
                if row:
                    self._symbol = row["symbol"]
        finally:
            self._suppress_select = False

    def _timeframe(self) -> str:
        return dict(TIMEFRAME_LABELS).get(self.tf_var.get(), "1d")

    def _on_select(self, row) -> None:
        if self._suppress_select:
            return
        if row and row.get("symbol"):
            self._symbol = row["symbol"]
            self._load_chart()
            self._ensure_history()

    # ---------------------------------------------------------- 历史日线
    def _ensure_history(self, force: bool = False) -> None:
        """只有当日快照时，后台补齐该股全部历史日线。"""
        symbol = self._symbol
        if not symbol or symbol in self._hist_loading:
            return
        if not force and symbol in self._hist_failed:
            return
        try:
            bars = self.c.services.market.history(symbol, "1d")
        except Exception:  # noqa: BLE001 - 图表不应因读库失败而崩
            bars = []
        if not force and len(bars) > 2:
            return
        self._hist_loading.add(symbol)
        self._hist_failed.pop(symbol, None)
        self.change_label.configure(text="正在补齐该股历史日线…", fg=theme.color("muted"))
        worker = threading.Thread(target=self._history_worker, args=(symbol,),
                                  daemon=True, name="history-%s" % symbol)
        worker.start()
        self.after(350, self._poll_history, symbol, worker)

    def _history_worker(self, symbol: str) -> None:
        today = date.today().strftime("%Y-%m-%d")
        try:
            result = self.c.services.market.sync_akshare(
                [symbol], start="1990-01-01", end=today, adjust="qfq", timeout=180)
        except Exception as exc:  # noqa: BLE001 - 线程内异常要转成结果
            result = {"ok": False, "errors": [str(exc)], "symbols": 0, "bars": 0}
        self._hist_result[symbol] = result

    def _poll_history(self, symbol: str, worker) -> None:
        if worker.is_alive():
            self.after(350, self._poll_history, symbol, worker)
            return
        try:
            self._hist_loading.discard(symbol)
            result = self._hist_result.pop(symbol, {}) or {}
            ok = int(result.get("bars") or 0) > 0
            if ok:
                self.refresh()
                if self._symbol == symbol:
                    self._load_chart()
                self.toast("已补齐 %s 的历史日线（%d 根）" % (symbol, result.get("bars", 0)),
                           "success")
            else:
                reason = (result.get("errors") or ["未知原因"])[0]
                self._hist_failed[symbol] = reason
                if self._symbol == symbol:
                    self.change_label.configure(
                        text="历史日线不可用：%s" % str(reason)[:60],
                        fg=theme.color("warning"))
                self.toast("补齐 %s 历史日线失败：%s" % (symbol, str(reason)[:60]), "error")
        finally:
            self._hist_loading.discard(symbol)

    def _retry_history(self) -> None:
        if not self._symbol:
            return
        self._ensure_history(force=True)

    def _load_chart(self) -> None:
        symbol = self._symbol
        if not symbol:
            return
        names = self.c.services.market.names()
        instrument = self.c.instruments.get(symbol)
        display_name = (names.get(symbol)
                        or (instrument.name if instrument else "")
                        or "")
        self.name_label.configure(text="%s  %s" % (symbol, display_name))
        timeframe = self._timeframe()
        bars = self.c.services.market.history(symbol, "1d")
        span = dict(RANGE_LABELS).get(self.range_var.get(), "3y")
        years = {"1y": 1, "3y": 3, "5y": 5}.get(span)
        if years and bars:
            cutoff = bars[-1].dt - timedelta(days=365 * years)
            bars = [bar for bar in bars if bar.dt >= cutoff]
        if timeframe != "1d" and bars:
            bars = resample(bars, timeframe)
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
        change = (latest.close / previous.close - 1.0) if previous else float(
            self._changes.get(symbol, 0.0))
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
