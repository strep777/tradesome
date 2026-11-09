"""概览页：账户、数据、最近回测与快捷入口。"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from ..widgets import Button, Card, DataTable, EmptyState, KpiCard, SectionHeader
from ..widgets.card import Divider
from .. import formatting as fmt
from .base import BasePage


class DashboardPage(BasePage):
    title = "概览"
    subtitle = "先看数据是否就绪，再看策略与回测结果；模拟盘只做研究，不接真实资金。"

    help_steps = [
        '先看四张卡片：权益、最近回测、数据量、风控。',
        '数据为 0 时，先去数据中心同步真实行情，或导入自己的 CSV。',
        '回测或模拟盘跑完后，这里会自动汇总。',
    ]

    def tour_targets(self) -> dict:
        return {"steps": getattr(self, "_guide_card", None)}

    def build(self) -> None:
        self.page_header()
        body = self.scroll_body()
        self._build_kpis(body)
        # 图表与侧栏卡给足固定高度，小屏幕交给页面滚动，避免卡片被裁。
        middle = tk.Frame(body, bg=theme.color("bg"), height=theme.px(268))
        middle.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), 0))
        middle.pack_propagate(False)
        self._build_chart(middle)
        self._build_side(middle)
        self._build_recent(body)

    # ---------------------------------------------------------- 结构

    def _build_kpis(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24), pady=(theme.px(8), 0))
        self.kpi_equity = KpiCard(row, "组合权益", "—", hint="最近回测或模拟盘")
        self.kpi_return = KpiCard(row, "最近回测收益", "—", hint="扣除费用后")
        self.kpi_data = KpiCard(row, "行情数据", "—", hint="标的数 / K 线根数")
        self.kpi_risk = KpiCard(row, "风控状态", "—", hint="下单前逐条检查")
        for card in (self.kpi_equity, self.kpi_return, self.kpi_data, self.kpi_risk):
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.px(12)))
        self.kpi_risk.pack_configure(padx=0)

    def _build_chart(self, parent) -> None:
        card = Card(parent)
        card.pack(side="left", fill="both", expand=True)
        header = SectionHeader(card.body, "权益曲线",
                               "最近一次回测的净值走势；可切换模拟盘会话。")
        header.pack(fill="x")
        self.chart_mode = tk.StringVar(value="backtest")
        Button(header.actions, "回测", command=lambda: self._switch("backtest"),
               variant="ghost", size="sm").pack(side="left")
        Button(header.actions, "模拟盘", command=lambda: self._switch("live"),
               variant="ghost", size="sm").pack(side="left", padx=(2, 0))
        from ..charts import LineChart
        self.chart = LineChart(card.body, height=150)
        self.chart.pack(fill="both", expand=True, pady=(theme.px(10), 0))
        self.chart_hint = tk.Label(card.body, text="", bg=theme.color("surface"),
                                   fg=theme.color("faint"), font=theme.font("small"),
                                   anchor="w")
        self.chart_hint.pack(fill="x", pady=(theme.px(6), 0))

    def _build_side(self, parent) -> None:
        side = tk.Frame(parent, bg=theme.color("bg"), width=theme.px(330))
        side.pack(side="left", fill="y", padx=(theme.px(12), 0))
        side.pack_propagate(False)

        quick = Card(side)
        quick.pack(fill="x")
        self._guide_card = quick
        tk.Label(quick.body, text="三步上手", bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("h2"),
                 anchor="w").pack(fill="x")
        self.steps = tk.Label(quick.body, text="", bg=theme.color("surface"),
                              fg=theme.color("muted"), font=theme.font("caption"),
                              justify="left", anchor="w", wraplength=theme.px(280))
        self.steps.pack(fill="x", pady=(theme.px(8), theme.px(10)))
        Button(quick.body, "① 同步真实行情",
               command=lambda: self.shell.navigate("data"),
               variant="primary", size="sm").pack(fill="x", pady=(0, 6))
        Button(quick.body, "② 打开回测", command=lambda: self.shell.navigate("backtest"),
               variant="secondary", size="sm").pack(fill="x", pady=(0, 6))
        Button(quick.body, "③ 启动模拟盘", command=lambda: self.shell.navigate("live"),
               variant="secondary", size="sm").pack(fill="x")


    def _build_recent(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", padx=theme.px(24),
                  pady=(theme.px(12), theme.px(16)))
        header = SectionHeader(card.body, "最近回测", "双击某一行可在回测页重新打开。")
        header.pack(fill="x")
        Button(header.actions, "全部记录", variant="ghost", size="sm",
               command=lambda: self.shell.navigate("backtest")).pack()
        columns = [("run_id", "运行编号", 120, "w"), ("created", "时间", 140, "w"),
                   ("strategy", "策略", 120, "w"),
                   ("total_return", "累计收益", 90, "e"),
                   ("max_drawdown", "最大回撤", 90, "e"),
                   ("sharpe", "夏普", 70, "e"),
                   ("trade_count", "交易", 60, "e")]
        self.table = DataTable(card.body, columns, height=3,
                               on_double=self._open_run)
        self.table.pack(fill="x", pady=(theme.px(8), 0))

    # ---------------------------------------------------------- 数据

    def refresh(self) -> None:
        summary = self.c.services.market.summary()
        self.kpi_data.set("%d / %s" % (summary["symbols"],
                                       "{:,}".format(summary["bars"])),
                          delta="就绪" if summary["bars"] else "待生成",
                          tone="success" if summary["bars"] else "warning")
        self.steps.configure(text=self._steps_text(summary["bars"]))

        live = self.c.services.live
        result = self.c.services.backtest.latest_result()
        if live.is_running():
            status = live.status()
            self.kpi_equity.set(fmt.money(status.get("equity", 0)),
                                delta="模拟盘运行中", tone="muted")
        elif result is not None:
            metrics = result.metrics or {}
            equity = (result.config.initial_cash
                      * (1.0 + float(metrics.get("total_return") or 0.0)))
            self.kpi_equity.set(fmt.money(equity), delta="按最近回测折算",
                                tone="muted")
        else:
            self.kpi_equity.set(fmt.money(self.c.config.trading.initial_cash),
                                delta="初始资金", tone="muted")

        if result is not None:
            metrics = result.metrics or {}
            self.kpi_return.set(fmt.pct(metrics.get("total_return"), signed=True),
                                delta="回撤 %s" % fmt.pct(metrics.get("max_drawdown")),
                                tone="up" if float(metrics.get("total_return") or 0) >= 0
                                else "down")
        else:
            self.kpi_return.set("—", delta="还没有回测记录", tone="muted")

        kill = bool(self.c.config.risk.kill_switch)
        self.kpi_risk.set("熔断" if kill else "%d 条规则" %
                          len(self.c.config.risk.enabled_rules),
                          delta="全部通过才能下单" if not kill else "已暂停开仓",
                          tone="danger" if kill else "success")
        self._refresh_chart()
        self._refresh_runs()

    def _refresh_chart(self) -> None:
        result = self.c.services.backtest.latest_result()
        if self.chart_mode.get() == "live":
            curve = self.c.services.live.equity_curve()
            series = [(row["dt"], float(row["equity"])) for row in curve]
            self.chart_hint.configure(text="模拟盘会话：%s" %
                                      (self.c.services.live.session_id or "未启动"))
        else:
            series = list(result.equity_curve) if result is not None else []
            self.chart_hint.configure(
                text=("回测 %s · %s" % (result.run_id, result.config.strategy_key)
                      if result is not None else "还没有回测数据"))
        self.chart.set_data(series)

    def _refresh_runs(self) -> None:
        rows = []
        for item in self.c.services.backtest.history(8):
            rows.append({
                "run_key": item.get("run_id") or "",
                "run_id": (item.get("run_id") or "")[:16],
                "created": fmt.date_text(item.get("created_at")),
                "strategy": item.get("strategy_name") or item.get("strategy_key") or "—",
                "total_return": fmt.pct(item.get("total_return"), signed=True),
                "max_drawdown": fmt.pct(item.get("max_drawdown")),
                "sharpe": ("%.2f" % item["sharpe"]) if item.get("sharpe") is not None else "—",
                "trade_count": item.get("trade_count", 0),
            })
        self.table.set_rows_colored(rows, tone_key=None)

    def _steps_text(self, bars: int) -> str:
        mark = lambda ok: "✓" if ok else "○"  # noqa: E731
        has_data = bars > 0
        has_backtest = self.c.services.backtest.history(1) != []
        has_live = self.c.services.live.is_running()
        return "\n".join([
            "%s 准备数据：%s" % (mark(has_data), "已完成" if has_data else "还没有行情"),
            "%s 跑一次回测：%s" % (mark(has_backtest),
                                 "已运行" if has_backtest else "建议先看默认参数"),
            "%s 开模拟盘：%s" % (mark(has_live),
                               "运行中" if has_live else "用同一套风控逐根推进"),
        ])

    # ---------------------------------------------------------- 交互

    def _switch(self, mode: str) -> None:
        self.chart_mode.set(mode)
        self._refresh_chart()

    def _generate(self) -> None:  # 兼容旧调用：统一跳到数据中心
        self.shell.navigate("data")

    def _open_run(self, row) -> None:
        if not row:
            return
        self.shell.navigate("backtest")
        page = getattr(self.shell, "_pages", {}).get("backtest")
        if page is not None and hasattr(page, "load_run"):
            page.load_run(row.get("run_key") or row.get("run_id"))
