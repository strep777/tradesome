"""实验室：调参试跑 + 策略代码编辑（保存前经过 AST 沙箱检查）。"""

from __future__ import annotations

import tkinter as tk
from datetime import timedelta
from tkinter import ttk

from .. import theme
from ...strategy.sandbox import check_source
from ..widgets import (Button, Card, DataTable, LabeledEntry, ParamForm,
                        SectionHeader)
from ..widgets.card import ScrollableFrame
from .. import formatting as fmt
from .base import BasePage

PERIODS = [("近 1 年", 365), ("近 2 年", 730), ("近 3 年", 1095), ("全部数据", 0)]


class LabPage(BasePage):
    title = "策略实验室"
    subtitle = "改参数、跑一小段历史，马上看结果；满意后再去回测页做完整检验。"

    help_steps = [
        '左侧选策略、标的与区间，右侧看试跑结果。',
        '改完参数点「试运行回测」，满意后去策略库保存成自己的策略。',
        '试跑只跑一个标的，完整检验请回回测页。',
    ]

    def tour_targets(self) -> dict:
        return {}

    def build(self) -> None:
        self._key = None
        self._form = None
        self._poll_job = None
        self._result_run = None
        self.page_header()
        tabs = tk.Frame(self, bg=theme.color("bg"))
        tabs.pack(fill="x", padx=theme.px(24), pady=(0, theme.px(8)))
        self._tab_buttons = {}
        button = Button(tabs, "参数试跑", variant="primary", size="sm")
        button.pack(side="left")
        self._tab_buttons["run"] = button
        tk.Label(tabs, text="要保存成自己的策略，去「策略库 → 新建策略」，不需要写文件。",
                 bg=theme.color("bg"), fg=theme.color("faint"),
                 font=theme.font("small")).pack(side="right")
        self.tab_host = tk.Frame(self, bg=theme.color("bg"))
        self.tab_host.pack(fill="both", expand=True, padx=theme.px(24),
                           pady=(0, theme.px(16)))
        self.tab_run = tk.Frame(self.tab_host, bg=theme.color("bg"))
        self._build_run_tab()
        self._current_tab = ""
        self._show_tab("run")

    def _show_tab(self, key: str) -> None:
        if key == self._current_tab:
            return
        self._current_tab = key
        self.tab_run.pack(fill="both", expand=True)
        for name, button in self._tab_buttons.items():
            button.set_variant("primary" if name == key else "secondary")

    # ---------------------------------------------------------- 试跑页

    def _build_run_tab(self) -> None:
        body = tk.Frame(self.tab_run, bg=theme.color("bg"))
        body.pack(fill="both", expand=True, pady=(theme.px(10), 0))
        left = Card(body, padding=14)
        left.configure(width=theme.px(380))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left.body, text="策略", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        self.strategy_var = tk.StringVar()
        self.strategy_combo = ttk.Combobox(left.body, textvariable=self.strategy_var,
                                           state="readonly", font=theme.font("body"))
        self.strategy_combo.pack(fill="x", pady=(4, theme.px(10)))
        self.strategy_combo.bind("<<ComboboxSelected>>", lambda e: self._load_strategy())

        tk.Label(left.body, text="标的", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        self.symbol_var = tk.StringVar()
        self.symbol_combo = ttk.Combobox(left.body, textvariable=self.symbol_var,
                                         state="readonly", font=theme.font("body"))
        self.symbol_combo.pack(fill="x", pady=(4, theme.px(10)))

        tk.Label(left.body, text="区间", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        self.period_var = tk.StringVar(value=PERIODS[0][0])
        ttk.Combobox(left.body, textvariable=self.period_var, state="readonly",
                     values=[p[0] for p in PERIODS],
                     font=theme.font("body")).pack(fill="x", pady=(4, theme.px(10)))

        self.param_holder = tk.Frame(left.body, bg=theme.color("surface"))
        self.param_holder.pack(fill="both", expand=True)

        actions = tk.Frame(left.body, bg=theme.color("surface"))
        actions.pack(fill="x", pady=(theme.px(10), 0))
        self.run_button = Button(actions, "试运行回测", command=self._run,
                                 variant="primary", size="sm")
        self.run_button.pack(side="left")
        Button(actions, "恢复默认", command=self._reset_params, variant="ghost",
               size="sm").pack(side="left", padx=(6, 0))

        right = tk.Frame(body, bg=theme.color("bg"))
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))
        self._build_run_result(right)

    def _build_run_result(self, parent) -> None:
        kpis = tk.Frame(parent, bg=theme.color("bg"))
        kpis.pack(fill="x")
        from ..widgets import KpiCard
        self.kpi_return = KpiCard(kpis, "累计收益", "—")
        self.kpi_dd = KpiCard(kpis, "最大回撤", "—")
        self.kpi_sharpe = KpiCard(kpis, "夏普", "—")
        self.kpi_trades = KpiCard(kpis, "交易次数", "—")
        for card in (self.kpi_return, self.kpi_dd, self.kpi_sharpe, self.kpi_trades):
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.px(8)))
        self.kpi_trades.pack_configure(padx=0)

        card = Card(parent)
        card.pack(fill="both", expand=True, pady=(theme.px(10), 0))
        header = SectionHeader(card.body, "试跑结果", "只跑选中标的与区间，用于快速判断参数方向。")
        header.pack(fill="x")
        self.run_status = tk.Label(header.actions, text="尚未运行", bg=theme.color("surface"),
                                   fg=theme.color("faint"), font=theme.font("small"))
        self.run_status.pack(side="right", padx=(0, theme.px(8)))
        from ..charts import LineChart
        self.chart = LineChart(card.body, height=196)
        self.chart.pack(fill="both", expand=True, pady=(theme.px(8), theme.px(6)))
        self.trades = DataTable(card.body, [
            ("open", "开仓", 90), ("close", "平仓", 90), ("symbol", "标的", 90),
            ("qty", "数量", 60, "e"), ("entry", "买入", 70, "e"),
            ("exit", "卖出", 70, "e"), ("pnl", "盈亏", 80, "e"),
            ("reason", "原因", 90),
        ], height=6)
        self.trades.pack(fill="x")
        self.progress = ttk.Progressbar(card.body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(theme.px(6), 0))

    # ---------------------------------------------------------- 代码页

    def _build_code_tab(self) -> None:
        body = tk.Frame(self.tab_code, bg=theme.color("bg"))
        body.pack(fill="both", expand=True, pady=(theme.px(10), 0))
        card = Card(body)
        card.pack(fill="both", expand=True)
        header = SectionHeader(card.body, "策略代码",
                               "继承 Strategy、声明 ParamSpec；保存前会做 AST 安全检查，"
                               "危险导入与调用会被拒绝。")
        header.pack(fill="x")
        Button(header.actions, "载入模板", command=self._load_template,
               variant="ghost", size="sm").pack(side="left")
        Button(header.actions, "校验", command=self._validate, variant="secondary",
               size="sm").pack(side="left", padx=(6, 0))
        self.key_entry = LabeledEntry(header.actions, "key", "", width=14)
        self.key_entry.pack(side="left", padx=(10, 6))
        Button(header.actions, "保存", command=self._save, variant="primary",
               size="sm").pack(side="left")

        editor_box = tk.Frame(card.body, bg=theme.color("surface_alt"),
                              highlightthickness=1,
                              highlightbackground=theme.color("border"))
        editor_box.pack(fill="both", expand=True, pady=(theme.px(10), theme.px(6)))
        self.editor = tk.Text(editor_box, bg=theme.color("surface_alt"),
                              fg=theme.color("text"), insertbackground=theme.color("text"),
                              font=(theme.MONO_FAMILY, 10), bd=0, relief="flat",
                              wrap="none", undo=True, padx=theme.px(10),
                              pady=theme.px(8))
        scroll = ttk.Scrollbar(editor_box, orient="vertical",
                               command=self.editor.yview)
        self.editor.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.editor.pack(side="left", fill="both", expand=True)
        self.code_status = tk.Label(card.body, text="", bg=theme.color("surface"),
                                    fg=theme.color("muted"), font=theme.font("small"),
                                    anchor="w", justify="left")
        self.code_status.pack(fill="x")

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        keys = self.c.strategies.keys()
        labels = []
        self._key_by_label = {}
        for key in keys:
            meta = self.c.strategies.meta(key)
            label = "%s · %s" % (meta.name, "自定义" if self.c.strategies.is_user(key) else meta.category)
            labels.append(label)
            self._key_by_label[label] = key
        self.strategy_combo.configure(values=labels)
        if self._key is None and keys:
            self._key = keys[0]
        if self._key:
            current = self._key
            for label, key in self._key_by_label.items():
                if key == current:
                    self.strategy_var.set(label)
                    break
        self._refresh_symbols()
        self._load_strategy()
        self._refresh_result()

    def _refresh_symbols(self) -> None:
        symbols = [item["symbol"] for item in self.c.services.market.universe()]
        labels = []
        self._symbol_by_label = {}
        for symbol in symbols:
            instrument = self.c.instruments.get(symbol)
            label = "%s %s" % (symbol, instrument.name if instrument else "")
            labels.append(label)
            self._symbol_by_label[label] = symbol
        self.symbol_combo.configure(values=labels)
        if not self.symbol_var.get() and labels:
            self.symbol_var.set(labels[0])

    def load_strategy(self, key: str) -> None:
        self._key = key
        self._load_strategy()

    def _load_strategy(self) -> None:
        label = self.strategy_var.get()
        key = getattr(self, "_key_by_label", {}).get(label) or self._key
        if not key:
            return
        self._key = key
        try:
            specs = self.c.services.strategy.params(key)
            defaults = self.c.services.strategy.defaults(key)
        except Exception as exc:  # noqa: BLE001
            self.error("读取策略参数失败", str(exc))
            return
        for child in self.param_holder.winfo_children():
            child.destroy()
        tk.Label(self.param_holder, text="参数", bg=theme.color("surface"),
                 fg=theme.color("accent"), font=theme.font("caption"),
                 anchor="w").pack(fill="x", pady=(0, 4))
        scroller = ScrollableFrame(self.param_holder, bg=theme.color("surface"))
        scroller.pack(fill="both", expand=True)
        self._form = ParamForm(scroller.body, specs, defaults,
                               bg=theme.color("surface"), columns=1)
        self._form.pack(fill="x")

    def _reset_params(self) -> None:
        if self._form is not None:
            self._form.reset_defaults()

    # ---------------------------------------------------------- 运行

    def _period_days(self) -> int:
        return dict(PERIODS).get(self.period_var.get(), 365)

    def _run(self) -> None:
        backtest = self.c.services.backtest
        if backtest.is_running():
            self.toast("已有一个回测在运行，请稍候", "warning")
            return
        symbol = getattr(self, "_symbol_by_label", {}).get(self.symbol_var.get())
        if not symbol:
            self.toast("请选择标的", "warning")
            return
        if not self._key:
            self.toast("请选择策略", "warning")
            return
        coverage = self.c.repos.bars.coverage(symbol, "1d")
        if not coverage or not coverage[1] or not coverage[2]:
            self.toast("该标的还没有数据，请先去数据中心生成", "warning")
            return
        first, last = coverage[1], coverage[2]
        days = self._period_days()
        start = first if days == 0 else max(first, last - timedelta(days=days))
        from ...core.types import BacktestConfig
        params = self._form.values() if self._form is not None else {}
        try:
            config = BacktestConfig(symbols=[symbol], start=start.date(),
                                    end=last.date(),
                                    initial_cash=self.c.config.trading.initial_cash,
                                    strategy_key=self._key, strategy_params=params)
            backtest.start(config)
        except Exception as exc:  # noqa: BLE001
            self.error("无法启动试跑", str(exc))
            return
        self.run_status.configure(text="运行中……", fg=theme.color("muted"))
        self.progress["value"] = 0
        self._poll()

    def _poll(self) -> None:
        backtest = self.c.services.backtest
        if backtest.is_running():
            self.progress["value"] = backtest.progress
            self._poll_job = self.after(200, self._poll)
            return
        self._poll_job = None
        self.progress["value"] = 100
        if backtest.last_error:
            self.run_status.configure(text="失败", fg=theme.color("danger"))
            self.error("试跑失败", str(backtest.last_error))
            return
        self._refresh_result()
        self.run_status.configure(text="完成", fg=theme.color("success"))

    def _refresh_result(self) -> None:
        result = self.c.services.backtest.latest_result()
        if result is None:
            self.chart.set_data([])
            return
        metrics = result.metrics or {}
        total = float(metrics.get("total_return") or 0.0)
        self.kpi_return.set(fmt.pct(total, signed=True),
                            delta="费用 %s" % fmt.money(metrics.get("total_fees")),
                            tone="up" if total >= 0 else "down")
        self.kpi_dd.set(fmt.pct(metrics.get("max_drawdown")), tone="warning")
        sharpe = metrics.get("sharpe")
        self.kpi_sharpe.set("%.2f" % sharpe if sharpe is not None else "—",
                            tone="up" if (sharpe or 0) > 1 else "muted")
        self.kpi_trades.set(str(metrics.get("trade_count", 0)),
                            delta="胜率 %s" % fmt.pct(metrics.get("win_rate")))
        self.chart.set_data(result.equity_curve,
                            secondary=result.benchmark_curve or None)
        rows = []
        for trade in result.trades:
            rows.append({
                "open": trade.open_dt.strftime("%Y-%m-%d"),
                "close": trade.close_dt.strftime("%Y-%m-%d"),
                "symbol": trade.symbol, "qty": trade.qty,
                "entry": fmt.money(trade.entry_price),
                "exit": fmt.money(trade.exit_price),
                "pnl": fmt.money(trade.pnl, sign=True),
                "reason": trade.exit_reason or "信号",
                "tone": trade.pnl,
            })
        self.trades.set_rows_colored(rows, tone_key="tone")

    # ---------------------------------------------------------- 代码工具

    def _load_template(self) -> None:
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.c.services.strategy.template())
        self.key_entry.set("my_strategy")
        self.code_status.configure(text="模板已载入。修改后先「校验」，再「保存」。",
                                   fg=theme.color("muted"))

    def _validate(self) -> bool:
        problems = check_source(self.editor.get("1.0", "end"))
        if problems:
            self.code_status.configure(
                text="发现 %d 个问题：%s" % (len(problems), "；".join(problems[:4])),
                fg=theme.color("danger"))
            return False
        self.code_status.configure(text="静态检查通过。", fg=theme.color("success"))
        return True

    def _save(self) -> None:
        if not self._validate():
            self.toast("代码未通过检查，未保存", "error")
            return
        key = self.key_entry.get().strip().lower()
        if not key:
            self.toast("请填写策略 key，例如 my_strategy", "warning")
            return
        try:
            path = self.c.services.strategy.save_user(
                key, self.editor.get("1.0", "end"), filename=key + ".py")
            self.toast("已保存 %s" % path, "success")
        except Exception as exc:  # noqa: BLE001
            self.error("保存失败", str(exc))
            return
        self._key = key
        self.refresh()

    def on_hide(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
