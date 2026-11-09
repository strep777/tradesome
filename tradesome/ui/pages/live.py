"""模拟盘页：用真实下单链路把历史行情按时间回放。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import theme
from ...core.types import SIDE_BUY, ST_FILLED, ST_REJECTED
from ..widgets import Button, Card, DataTable, KpiCard, LabeledEntry, SectionHeader
from .. import formatting as fmt
from .base import BasePage

STATUS_LABELS = {"idle": "未开始", "running": "运行中", "paused": "已暂停",
                 "stopped": "已停止", "finished": "已完成"}
SPEEDS = [("1×", 1.0), ("2×", 2.0), ("5×", 5.0), ("10×", 10.0)]
DAYS = [("120 个交易日", 120), ("250 个交易日", 250), ("500 个交易日", 500)]
ORDER_STATUS = {"PENDING": "待报", "SUBMITTED": "已报", "PARTIAL": "部成",
                "FILLED": "已成", "CANCELLED": "已撤", "REJECTED": "已拒"}


class LivePage(BasePage):
    title = "模拟盘"
    subtitle = "同一套策略、撮合、费用与风控，只是把历史 K 线按节奏播放；不会下真实订单。"

    help_steps = [
        '选策略、速度与回放区间。',
        '点「开始」后按真实节奏回放，持仓与订单实时更新。',
        '「停止」结束会话；模拟盘不碰真实资金。',
    ]

    def tour_targets(self) -> dict:
        return {"start": getattr(self, "start_button", None)}

    def build(self) -> None:
        self._poll_job = None
        self._ticks = 0
        self._curve = []
        self._session = None
        header = self.page_header()
        self.status_label = tk.Label(header, text="未开始", bg=theme.color("surface_alt"),
                                     fg=theme.color("muted"), font=theme.font("caption"),
                                     padx=theme.px(10), pady=theme.px(3))
        self.status_label.pack(side="right", padx=(0, theme.px(8)))
        Button(header, "刷新", command=self.refresh, variant="ghost",
               size="sm").pack(side="right", padx=(0, theme.px(8)))

        body = self.scroll_body()
        self._build_controls(body)
        self._build_kpis(body)
        self._build_chart(body)
        self._build_tables(body)

    def _build_controls(self, parent) -> None:
        card = Card(parent, padding=12)
        card.pack(fill="x", padx=theme.px(24), pady=(0, theme.px(10)))
        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x")
        tk.Label(row, text="策略", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption")).pack(side="left")
        self.strategy_var = tk.StringVar()
        self.strategy_combo = ttk.Combobox(row, textvariable=self.strategy_var,
                                           state="readonly", width=22,
                                           font=theme.font("body"))
        self.strategy_combo.pack(side="left", padx=(6, theme.px(14)))
        self.cash_entry = LabeledEntry(row, "初始资金", "", width=12)
        self.cash_entry.pack(side="left", padx=(0, theme.px(10)))
        tk.Label(row, text="速度", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption")).pack(side="left")
        self.speed_var = tk.StringVar(value=SPEEDS[1][0])
        ttk.Combobox(row, textvariable=self.speed_var, state="readonly", width=5,
                     values=[label for label, _ in SPEEDS],
                     font=theme.font("body")).pack(side="left", padx=(6, theme.px(10)))
        tk.Label(row, text="区间", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption")).pack(side="left")
        self.days_var = tk.StringVar(value=DAYS[1][0])
        ttk.Combobox(row, textvariable=self.days_var, state="readonly", width=14,
                     values=[label for label, _ in DAYS],
                     font=theme.font("body")).pack(side="left", padx=(6, theme.px(16)))

        self.start_button = Button(row, "开始", command=self._start,
                                   variant="primary", size="sm")
        self.start_button.pack(side="left")
        self.pause_button = Button(row, "暂停", command=self._pause,
                                   variant="secondary", size="sm")
        self.pause_button.pack(side="left", padx=(6, 0))
        self.resume_button = Button(row, "继续", command=self._resume,
                                    variant="secondary", size="sm")
        self.resume_button.pack(side="left", padx=(6, 0))
        self.stop_button = Button(row, "停止", command=self._stop,
                                  variant="danger", size="sm")
        self.stop_button.pack(side="left", padx=(6, 0))

    def _build_kpis(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24))
        self.kpi_equity = KpiCard(row, "权益", "—")
        self.kpi_cash = KpiCard(row, "可用现金", "—")
        self.kpi_pnl = KpiCard(row, "浮动盈亏", "—")
        self.kpi_progress = KpiCard(row, "回放进度", "—")
        for card in (self.kpi_equity, self.kpi_cash, self.kpi_pnl, self.kpi_progress):
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.px(10)))
        self.kpi_progress.pack_configure(padx=0)

    def _build_chart(self, parent) -> None:
        card = Card(parent, padding=12)
        card.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), 0))
        head = SectionHeader(card.body, "权益曲线", "每 5 个交易日写一次快照；曲线来自会话数据库。")
        head.pack(fill="x")
        from ..charts import LineChart
        self.chart = LineChart(card.body, height=96)
        self.chart.pack(fill="x", pady=(theme.px(6), 0))

    def _build_tables(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), theme.px(8)))
        left = Card(row, padding=12)
        left.pack(side="left", fill="both", expand=True)
        SectionHeader(left.body, "持仓", "T+1：当日买入次日可卖。").pack(fill="x")
        self.positions = DataTable(left.body, [
            ("symbol", "标的", 92), ("qty", "数量", 60, "e"),
            ("available", "可卖", 60, "e"), ("cost", "成本", 70, "e"),
            ("last", "现价", 70, "e"), ("pnl", "盈亏", 78, "e"),
            ("pct", "比例", 62, "e"),
        ], height=3)
        self.positions.pack(fill="both", expand=True, pady=(theme.px(6), 0))

        right = Card(row, padding=12)
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(10), 0))
        SectionHeader(right.body, "订单", "被风控拒绝的订单也会留痕。").pack(fill="x")
        self.orders = DataTable(right.body, [
            ("time", "时间", 100), ("symbol", "标的", 88),
            ("side", "方向", 44, "center"), ("qty", "数量", 56, "e"),
            ("price", "价格", 64, "e"), ("status", "状态", 52, "center"),
            ("reason", "说明", 110),
        ], height=3)
        self.orders.pack(fill="both", expand=True, pady=(theme.px(6), 0))

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        self._refresh_strategies()
        if not self.cash_entry.get():
            self.cash_entry.set("%.0f" % self.c.config.trading.initial_cash)
        self._refresh_status()
        self._refresh_tables()
        if self.c.services.live.is_running():
            self._schedule_poll()

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

    def _refresh_status(self) -> None:
        status = self.c.services.live.status()
        state = status.get("status", "idle")
        self.status_label.configure(text="● " + STATUS_LABELS.get(state, state),
                                    fg=theme.color("success" if state == "running"
                                                   else "warning" if state == "paused"
                                                   else "muted"))
        if state == "idle":
            self._set_buttons(running=False, paused=False)
            return
        total = status.get("total") or 1
        index = status.get("index") or 0
        equity = status.get("equity") or 0.0
        cash = status.get("cash") or 0.0
        pnl = equity - float(status.get("initial_cash") or self.c.config.trading.initial_cash)
        self.kpi_equity.set(fmt.money(equity))
        self.kpi_cash.set(fmt.money(cash))
        self.kpi_pnl.set(fmt.money(pnl, sign=True),
                         tone="up" if pnl >= 0 else "down")
        self.kpi_progress.set("%d / %d" % (index, total),
                              delta="%.0f%%" % (100.0 * index / max(1, total)))
        self._set_buttons(running=state == "running", paused=state == "paused")

    def _set_buttons(self, running: bool, paused: bool) -> None:
        self.start_button.set_enabled(not running)
        self.pause_button.set_enabled(running)
        self.resume_button.set_enabled(paused)
        self.stop_button.set_enabled(running or paused)

    def _refresh_tables(self) -> None:
        positions = self.c.services.live.positions()
        rows = []
        for item in positions:
            rows.append({
                "symbol": item["symbol"], "qty": item["qty"],
                "available": item["available"],
                "cost": fmt.money(item["avg_cost"]), "last": fmt.money(item["last"]),
                "pnl": fmt.money(item["pnl"], sign=True),
                "pct": fmt.pct(item["pnl_pct"], signed=True),
                "tone": item["pnl"],
            })
        self.positions.set_rows_colored(rows, tone_key="tone")

        orders = []
        for order in reversed(self.c.services.live.orders(60)):
            orders.append({
                "time": fmt.date_text(order.created_at),
                "symbol": order.symbol,
                "side": "买入" if order.side == SIDE_BUY else "卖出",
                "qty": order.qty, "price": fmt.money(order.avg_fill_price
                                                     or order.limit_price or 0),
                "status": ORDER_STATUS.get(order.status, order.status),
                "reason": order.reason or "—",
            })
        self.orders.set_rows(orders)
        self._refresh_curve()

    def _refresh_curve(self) -> None:
        session = self.c.services.live.session_id
        if session != self._session:
            self._session = session
            self._curve = []
        if not session:
            self.chart.set_data([])
            return
        stored = self.c.services.live.equity_curve()
        series = [(row["dt"], float(row["equity"])) for row in stored]
        if self._curve:
            series = series + self._curve
        self.chart.set_data(series)

    # ---------------------------------------------------------- 事件

    def on_event(self, topic: str, payload: dict) -> None:
        if topic == "live.tick":
            self._ticks += 1
            equity = float(payload.get("equity") or 0.0)
            cash = float(payload.get("cash") or 0.0)
            pnl = float(payload.get("pnl") or 0.0)
            total = payload.get("total") or 1
            index = payload.get("index") or 0
            self.kpi_equity.set(fmt.money(equity))
            self.kpi_cash.set(fmt.money(cash))
            self.kpi_pnl.set(fmt.money(pnl, sign=True), tone="up" if pnl >= 0 else "down")
            self.kpi_progress.set("%d / %d" % (index, total), delta="%.0f%%" % (
                100.0 * index / max(1, total)))
            moment = payload.get("dt")
            if moment is not None:
                self._curve.append((moment, equity))
            if self._ticks % 5 == 0:
                self._refresh_curve()
        elif topic in ("live.started", "live.stopped"):
            self._session = None
            self._curve = []
            self.refresh()
            self.shell.refresh_status()
        elif topic == "order.filled":
            self._refresh_tables()

    # ---------------------------------------------------------- 控制

    def _start(self) -> None:
        label = self.strategy_var.get()
        key = getattr(self, "_strategy_labels", {}).get(label)
        if not key:
            self.toast("请选择策略", "warning")
            return
        try:
            cash = float(self.cash_entry.get() or 0)
        except ValueError:
            self.error("初始资金有误", "请填写数字，例如 100000")
            return
        speed = dict(SPEEDS).get(self.speed_var.get(), 2.0)
        days = dict(DAYS).get(self.days_var.get(), 250)
        try:
            session_id = self.c.services.live.start(
                key, symbols=None, days=days, speed=speed, initial_cash=cash)
        except Exception as exc:  # noqa: BLE001
            self.error("无法启动模拟盘", str(exc))
            return
        self._session = session_id
        self._curve = []
        self.toast("模拟盘已启动：%s" % session_id, "success")
        self.refresh()
        self._schedule_poll()

    def _pause(self) -> None:
        self.c.services.live.pause()
        self._refresh_status()

    def _resume(self) -> None:
        self.c.services.live.resume()
        self._schedule_poll()
        self._refresh_status()

    def _stop(self) -> None:
        if not self.c.services.live.is_running():
            return
        self.shell.confirm("停止模拟盘", "停止后本次会话将归档，不能继续回放。确定停止吗？",
                           self._do_stop, confirm_text="停止")

    def _do_stop(self) -> None:
        try:
            self.c.services.live.stop()
        except Exception as exc:  # noqa: BLE001
            self.error("停止失败", str(exc))
        self.refresh()
        self.shell.refresh_status()

    def _schedule_poll(self) -> None:
        if self._poll_job is not None:
            return
        self._poll_job = self.after(400, self._poll)

    def _poll(self) -> None:
        self._poll_job = None
        if not self.c.services.live.is_running():
            self._refresh_status()
            self._refresh_tables()
            self.shell.refresh_status()
            return
        self._refresh_status()
        self._poll_job = self.after(500, self._poll)

    def on_hide(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
