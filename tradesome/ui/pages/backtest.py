"""回测页：完整配置一次历史检验，查看指标、曲线与逐笔交易。"""

from __future__ import annotations

import re
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import ttk

from .. import theme
from ..widgets import (Button, Card, DataTable, KpiCard, LabeledEntry,
                        SectionHeader, Toggle)
from ..widgets.card import ScrollableFrame
from .. import formatting as fmt
from .base import BasePage
from ...core.instruments import normalize_symbol
from ...security.validation import validate_symbol


class BacktestPage(BasePage):
    title = "回测"
    subtitle = "所有代码写完后才做验证；这里的回测同样使用下一根 K 线成交、A 股费用与风控。"

    help_steps = [
        '先选策略，再勾选 1–3 个标的。',
        '按需改日期区间与初始资金；风控建议保持开启。',
        '点「开始回测」，看完 6 个指标再点「逐笔交易」看细节。',
    ]

    def tour_targets(self) -> dict:
        return {"start": getattr(self, "start_button", None)}

    def build(self) -> None:
        self._result = None
        self._poll_job = None
        self._error_reported = False
        self._symbol_vars = {}
        header = self.page_header()
        bar = tk.Frame(header, bg=theme.color("bg"))
        bar.pack(side="right")
        Button(bar, "导出报告", command=self._export, variant="secondary",
               size="sm").pack(side="left", padx=(0, 6))
        Button(bar, "刷新", command=lambda: self.refresh(), variant="ghost",
               size="sm").pack(side="left")

        body = tk.Frame(self, bg=theme.color("bg"))
        body.pack(fill="both", expand=True, padx=theme.px(24), pady=(0, theme.px(16)))
        self._build_form(body)
        self._build_result(body)

    # ---------------------------------------------------------- 配置表单

    def _build_form(self, parent) -> None:
        card = Card(parent, padding=14)
        card.configure(width=theme.px(330))
        card.pack(side="left", fill="y")
        card.pack_propagate(False)
        SectionHeader(card.body, "回测配置").pack(fill="x")

        tk.Label(card.body, text="策略", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x", pady=(theme.px(12), 0))
        self.strategy_var = tk.StringVar()
        self.strategy_combo = ttk.Combobox(card.body, textvariable=self.strategy_var,
                                           state="readonly", font=theme.font("body"))
        self.strategy_combo.pack(fill="x", pady=(4, theme.px(8)))

        tk.Label(card.body, text="标的（可多选）", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        symbols_box = tk.Frame(card.body, bg=theme.color("surface"))
        symbols_box.pack(fill="x", pady=(4, theme.px(8)))
        scroller = ScrollableFrame(symbols_box, bg=theme.color("surface"))
        scroller.pack(fill="both", expand=True)
        scroller.canvas.configure(height=theme.px(88))
        self._symbol_holder = scroller.body

        self.extra_entry = LabeledEntry(card.body, "额外代码（逗号分隔）", "",
                                        hint="", width=12)
        self.extra_entry.pack(fill="x", pady=(0, theme.px(6)))

        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x", pady=(0, theme.px(6)))
        self.start_entry = LabeledEntry(row, "开始日期", "", width=12)
        self.start_entry.pack(side="left", fill="x", expand=True)
        self.end_entry = LabeledEntry(row, "结束日期", "", width=12)
        self.end_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.cash_entry = LabeledEntry(card.body, "初始资金", "100000", width=12)
        self.cash_entry.pack(fill="x", pady=(0, theme.px(6)))

        self.risk_var = tk.BooleanVar(value=True)
        Toggle(card.body, "启用风控闸门（推荐）", self.risk_var).pack(
            anchor="w", pady=(0, theme.px(6)))
        self.ai_var = tk.BooleanVar(value=False)
        Toggle(card.body, "计入基准（沪深300）", self.ai_var).pack(anchor="w",
                                                              pady=(0, theme.px(10)))

        actions = tk.Frame(card.body, bg=theme.color("surface"))
        actions.pack(fill="x")
        self.start_button = Button(actions, "开始回测", command=self._start,
                                   variant="primary", size="sm")
        self.start_button.pack(side="left")
        self.cancel_button = Button(actions, "取消", command=self._cancel,
                                    variant="ghost", size="sm")
        self.cancel_button.pack(side="left", padx=(6, 0))
        self.progress = ttk.Progressbar(card.body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(theme.px(10), 4))
        self.run_status = tk.Label(card.body, text="尚未运行", bg=theme.color("surface"),
                                   fg=theme.color("faint"), font=theme.font("small"),
                                   anchor="w", justify="left", wraplength=theme.px(290))
        self.run_status.pack(fill="x")

    def _build_result(self, parent) -> None:
        right = tk.Frame(parent, bg=theme.color("bg"))
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))

        kpi_row = tk.Frame(right, bg=theme.color("bg"))
        kpi_row.pack(fill="x")
        self.kpis = {}
        for key, label in (("total_return", "累计收益"), ("annual_return", "年化收益"),
                           ("max_drawdown", "最大回撤"), ("sharpe", "夏普"),
                           ("win_rate", "胜率"), ("trade_count", "交易次数")):
            card = KpiCard(kpi_row, label, "—")
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.px(8)))
            self.kpis[key] = card
        self.kpis["trade_count"].pack_configure(padx=0)

        price_card = Card(right, padding=12)
        price_card.pack(fill="both", expand=True, pady=(theme.px(10), 0))
        head = SectionHeader(price_card.body, "权益与回撤",
                             "蓝色为策略权益，灰色虚线为基准；回撤单独画在下方。")
        head.pack(fill="x")
        from ..charts import LineChart
        self.equity_chart = LineChart(price_card.body, height=150)
        self.equity_chart.pack(fill="both", expand=True, pady=(theme.px(6), 2))
        self.dd_chart = LineChart(price_card.body, height=70, mode="drawdown",
                                  fill=True)
        self.dd_chart.pack(fill="x", pady=(0, theme.px(4)))

        trades_card = Card(right, padding=12)
        trades_card.pack(fill="x", pady=(theme.px(10), 0))
        SectionHeader(trades_card.body, "逐笔交易",
                      "盈亏已扣除佣金、印花税、过户费与滑点。").pack(fill="x")
        self.trades = DataTable(trades_card.body, [
            ("open", "开仓", 84), ("close", "平仓", 84), ("symbol", "标的", 88),
            ("qty", "数量", 58, "e"), ("entry", "买入", 66, "e"),
            ("exit", "卖出", 66, "e"), ("pnl", "盈亏", 76, "e"),
            ("days", "持有", 44, "e"), ("reason", "原因", 80),
        ], height=3)
        self.trades.pack(fill="x", pady=(theme.px(6), 0))

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        self._refresh_strategies()
        self._refresh_symbols()
        self._refresh_dates()
        if self._result is None:
            result = self.c.services.backtest.latest_result()
            if result is not None:
                self._show_result(result)
        live = self.c.services.backtest
        self.progress["value"] = live.progress
        if live.is_running():
            self._poll()

    def _refresh_strategies(self) -> None:
        self._strategy_labels = {}
        labels = []
        for key in self.c.strategies.keys():
            meta = self.c.strategies.meta(key)
            label = "%s · %s" % (meta.name, meta.category)
            labels.append(label)
            self._strategy_labels[label] = key
        self.strategy_combo.configure(values=labels)
        if not self.strategy_var.get() and labels:
            self.strategy_var.set(labels[0])

    def _refresh_symbols(self) -> None:
        existing = set(self._symbol_vars)
        for child in self._symbol_holder.winfo_children():
            child.destroy()
        self._symbol_vars.clear()
        universe = self.c.services.market.universe()
        counts = {}
        for item in universe:
            cov = self.c.repos.bars.coverage(item["symbol"], "1d")
            counts[item["symbol"]] = cov[0] if cov else 0
        ranked = sorted(universe, key=lambda item: -counts.get(item["symbol"], 0))
        defaults = [item["symbol"] for item in ranked
                    if counts.get(item["symbol"], 0) >= 120][:3]
        if not defaults:
            defaults = [item["symbol"] for item in universe[:3]]
        for item in universe:
            var = tk.BooleanVar(value=item["symbol"] in defaults if not existing
                                else item["symbol"] in existing)
            label = "%s %s" % (item["symbol"], item["name"])
            if counts.get(item["symbol"]):
                label += "  (%d)" % counts[item["symbol"]]
            Toggle(self._symbol_holder, label, var).pack(anchor="w", pady=theme.px(2))
            self._symbol_vars[item["symbol"]] = var

    def _refresh_dates(self) -> None:
        if self.start_entry.get() and self.end_entry.get():
            return
        ranges = []
        for symbol in self._selected_symbols():
            cov = self.c.repos.bars.coverage(symbol, "1d")
            if cov and cov[0] >= 2 and cov[1] and cov[2]:
                ranges.append((cov[1], cov[2]))
        if ranges:
            start = max(r[0] for r in ranges)
            end = min(r[1] for r in ranges)
        else:
            end = datetime.now()
            start = end - timedelta(days=365 * 3)
        if start >= end:
            end = datetime.now()
            start = end - timedelta(days=365 * 3)
        if not self.start_entry.get():
            self.start_entry.set(start.strftime("%Y-%m-%d"))
        if not self.end_entry.get():
            self.end_entry.set(end.strftime("%Y-%m-%d"))

    # ---------------------------------------------------------- 运行

    def _selected_symbols(self) -> list:
        symbols = [symbol for symbol, var in self._symbol_vars.items() if var.get()]
        raw = ""
        try:
            raw = self.extra_entry.get() if hasattr(self, "extra_entry") else ""
        except Exception:  # noqa: BLE001
            raw = ""
        for token in re.split(r"[,，\s]+", (raw or "").strip()):
            if not token:
                continue
            try:
                symbol = validate_symbol(normalize_symbol(token.upper()))
            except Exception as exc:  # noqa: BLE001
                raise ValueError("代码「%s」无效：%s" % (token, exc))
            if symbol not in symbols:
                symbols.append(symbol)
        return symbols

    def _parse_date(self, text: str, label: str):
        try:
            return datetime.strptime(text.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("%s 格式应为 YYYY-MM-DD" % label)

    def _start(self) -> None:
        service = self.c.services.backtest
        self._error_reported = False
        if service.is_running():
            self.toast("回测正在运行，可先取消", "warning")
            return
        try:
            symbols = self._selected_symbols()
        except ValueError as exc:
            self.error("代码有误", str(exc))
            return
        if not symbols:
            self.toast("至少选择一个标的", "warning")
            return
        sparse = []
        for symbol in symbols:
            cov = self.c.repos.bars.coverage(symbol, "1d")
            if not cov or cov[0] < 2:
                sparse.append(symbol)
        if sparse:
            self.error(
                "标的缺少历史 K 线",
                "以下标的在本地不足 2 根日线：%s\n请先到「数据中心」同步它们的全部历史。"
                % "、".join(sparse[:6]))
            return
        try:
            start = self._parse_date(self.start_entry.get(), "开始日期")
            end = self._parse_date(self.end_entry.get(), "结束日期")
            if start >= end:
                raise ValueError("开始日期必须早于结束日期")
            cash = float(self.cash_entry.get() or 0)
            if cash < 1000:
                raise ValueError("初始资金至少 1000 元")
        except ValueError as exc:
            self.error("配置有误", str(exc))
            return
        label = self.strategy_var.get()
        key = self._strategy_labels.get(label)
        if not key:
            self.toast("请选择策略", "warning")
            return
        params = self.c.services.strategy.defaults(key)
        from ...core.types import BacktestConfig
        try:
            config = BacktestConfig(
                symbols=symbols, start=start, end=end, initial_cash=cash,
                strategy_key=key, strategy_params=params,
                risk_enabled=bool(self.risk_var.get()))
            service.start(config)
        except Exception as exc:  # noqa: BLE001
            self.error("无法开始回测", str(exc))
            return
        self._result = None
        self.run_status.configure(text="正在加载数据并逐根推进……",
                                  fg=theme.color("muted"))
        self.progress["value"] = 0
        self._poll()

    def _cancel(self) -> None:
        if self.c.services.backtest.is_running():
            self.c.services.backtest.cancel()
            self.run_status.configure(text="正在取消……", fg=theme.color("warning"))

    def _poll(self) -> None:
        service = self.c.services.backtest
        if service.is_running():
            self.progress["value"] = service.progress
            self.run_status.configure(text="运行中…… %d%%" % int(service.progress),
                                      fg=theme.color("muted"))
            self._poll_job = self.after(200, self._poll)
            return
        self._poll_job = None
        self.progress["value"] = 100
        if service.last_error:
            self.run_status.configure(text="失败：%s" % service.last_error,
                                      fg=theme.color("danger"))
            if not self._error_reported:
                self._error_reported = True
                self.toast("回测失败：%s" % str(service.last_error)[:80], "error")
            return
        result = service.latest_result()
        if result is not None:
            count = int((result.metrics or {}).get("trade_count", 0) or 0)
            if count == 0:
                self.run_status.configure(
                    text="完成，但没有产生交易：区间可能太短，或策略条件太严。",
                    fg=theme.color("warning"))
            else:
                self.run_status.configure(
                    text="完成：%d 笔交易，用时 %.2fs" % (len(result.trades),
                                                       result.duration_sec),
                    fg=theme.color("success"))
            self._show_result(result)
            self.shell.refresh_status()

    # ---------------------------------------------------------- 结果

    def _show_result(self, result) -> None:
        self._result = result
        metrics = result.metrics or {}
        self.kpis["total_return"].set(
            fmt.pct(metrics.get("total_return"), signed=True),
            delta="年化 %s" % fmt.pct(metrics.get("annual_return")),
            tone="up" if float(metrics.get("total_return") or 0) >= 0 else "down")
        self.kpis["annual_return"].set(fmt.pct(metrics.get("annual_return"), signed=True))
        self.kpis["max_drawdown"].set(fmt.pct(metrics.get("max_drawdown")),
                                      tone="warning")
        sharpe = metrics.get("sharpe")
        self.kpis["sharpe"].set("%.2f" % sharpe if sharpe is not None else "—",
                                tone="up" if (sharpe or 0) > 1 else "muted")
        self.kpis["win_rate"].set(fmt.pct(metrics.get("win_rate")),
                                  delta="盈亏比 %s" % _num(metrics.get("payoff_ratio")))
        self.kpis["trade_count"].set(str(metrics.get("trade_count", 0)),
                                     delta="费用 %s" % fmt.money(metrics.get("total_fees")))
        self.equity_chart.set_data(result.equity_curve,
                                   secondary=result.benchmark_curve or None)
        self.dd_chart.set_data(_drawdown_series(result), mode="drawdown")
        rows = []
        for trade in result.trades:
            rows.append({
                "open": trade.open_dt.strftime("%Y-%m-%d"),
                "close": trade.close_dt.strftime("%Y-%m-%d"),
                "symbol": trade.symbol, "qty": trade.qty,
                "entry": fmt.money(trade.entry_price),
                "exit": fmt.money(trade.exit_price),
                "pnl": fmt.money(trade.pnl, sign=True),
                "days": trade.holding_days, "reason": trade.exit_reason or "信号",
                "tone": trade.pnl,
            })
        self.trades.set_rows_colored(rows, tone_key="tone")
        for card in self.kpis.values():
            pass

    def load_run(self, run_id: str) -> None:
        try:
            result = self.c.services.backtest.load(run_id)
        except Exception as exc:  # noqa: BLE001
            self.error("打开回测失败", str(exc))
            return
        if result is None:
            self.toast("找不到这次回测", "warning")
            return
        self._show_result(result)
        self.run_status.configure(text="已载入历史回测 %s" % run_id,
                                  fg=theme.color("muted"))

    # ---------------------------------------------------------- 外部预设

    def preset_strategy(self, key: str) -> None:
        for label, value in getattr(self, "_strategy_labels", {}).items():
            if value == key:
                self.strategy_var.set(label)
                return

    def preset_symbols(self, symbols) -> None:
        wanted = set(symbols)
        for symbol, var in self._symbol_vars.items():
            var.set(symbol in wanted)

    # ---------------------------------------------------------- 导出

    def _export(self) -> None:
        if self._result is None:
            self.toast("先运行一次回测再导出", "warning")
            return
        try:
            paths = self.c.services.report.export_all(self._result)
            self.toast("报告已导出：%s" % paths.get("markdown", ""), "success")
        except Exception as exc:  # noqa: BLE001
            self.error("导出失败", str(exc))

    def on_hide(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None


def _num(value) -> str:
    return "%.2f" % value if isinstance(value, (int, float)) else "—"


def _drawdown_series(result):
    curve = list(result.equity_curve or [])
    peak = None
    out = []
    for moment, equity in curve:
        peak = equity if peak is None else max(peak, equity)
        dd = (equity / peak - 1.0) if peak else 0.0
        out.append((moment, dd))
    return out
