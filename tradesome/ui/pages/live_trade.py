"""实盘页：连接柜台、手动下单、持仓与委托回报。

三条硬规则写进界面：
1. 每次启动都要重新解锁，勾选风险提示并输入确认词；
2. 默认只发限价单，市价单要在设置里显式开启；
3. 所有委托都走同一套风控与审计，紧急停止随时可用。
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Dict, List, Optional

from .. import formatting as fmt
from .. import theme
from ...core.types import SIDE_BUY, SIDE_SELL
from ...core.errors import TradingError
from ..widgets import (Button, Card, DataTable, KpiCard, LabeledEntry,
                       SectionHeader, Toggle)
from .base import BasePage

UNLOCK_PHRASE = "实盘交易"
GATEWAYS = [("模拟柜台（本地演练）", "sim"), ("迅投 QMT（miniQMT）", "qmt")]
QMT_ACCOUNT_TYPES = [("普通股票账户", "STOCK"), ("融资融券账户", "CREDIT")]
SIDES = [("买入", SIDE_BUY), ("卖出", SIDE_SELL)]
STATUS_LABELS = {
    "idle": "未连接", "connecting": "连接中", "connected": "已连接",
    "disconnecting": "断开中", "error": "连接失败", "disabled": "未启用",
}
ORDER_STATUS = {"PENDING": "待报", "SUBMITTED": "已报", "PARTIAL": "部成",
                "FILLED": "已成", "CANCELLED": "已撤", "REJECTED": "已拒",
                "UNKNOWN": "待确认"}
ORDER_STATUS_OPEN = {"PENDING", "SUBMITTED", "PARTIAL", "UNKNOWN"}


class LiveTradePage(BasePage):
    title = "实盘"
    subtitle = "同一套策略和风控，接真实柜台；每一笔委托都会留痕，可用紧急停止一键熔断。"

    help_steps = [
        '先在「设置 → 实盘」启用，再回到这里输入确认词解锁。',
        '没有 QMT 时选「模拟柜台」演练，流程与真实一致。',
        '连接后填标的、数量与价格，二次确认后提交；紧急停止会先撤单再断开。',
    ]

    def tour_targets(self) -> dict:
        return {"gate": getattr(self, "gate_card", None)}

    def build(self) -> None:
        self._gate_job = None
        self._poll_job = None
        self._refresh_job = None
        self._last_tick = None
        header = self.page_header()
        self.status_label = tk.Label(header, text="未连接", bg=theme.color("surface_alt"),
                                     fg=theme.color("muted"), font=theme.font("caption"),
                                     padx=theme.px(10), pady=theme.px(3))
        self.status_label.pack(side="right", padx=(0, theme.px(8)))
        Button(header, "刷新", command=self.refresh, variant="ghost",
               size="sm").pack(side="right", padx=(0, theme.px(8)))

        body = self.scroll_body()
        self._build_gate(body)
        self._build_connect(body)
        self._build_kpis(body)
        self._build_order_and_positions(body)
        self._build_tables(body)
        self._build_auto(body)

    # ---------------------------------------------------------- 解锁

    def _build_gate(self, parent) -> None:
        self.gate_card = Card(parent, padding=14)
        self.gate_card.pack(fill="x", padx=theme.px(24), pady=(0, theme.px(10)))
        self._render_gate()

    def _render_gate(self) -> None:
        holder = self.gate_card.body
        for child in holder.winfo_children():
            child.destroy()
        service = self.c.services.live_trade
        live = self.c.config.live
        if not live.enabled:
            self._gate_title(holder, "实盘功能未启用", "text")
            tk.Label(holder, text="先在「设置 → 实盘」中打开开关，再回到这里解锁。",
                     bg=theme.color("surface"), fg=theme.color("muted"),
                     font=theme.font("caption"), anchor="w").pack(fill="x", pady=(4, 8))
            Button(holder, "去设置", command=lambda: self.shell.navigate("settings"),
                   variant="secondary", size="sm").pack(anchor="w")
            return
        if service.armed:
            self._gate_title(holder, "已解锁：本次运行内有效", "success")
            row = tk.Frame(holder, bg=theme.color("surface"))
            row.pack(fill="x", pady=(6, 0))
            tk.Label(row, text="下单前请再核对标的、方向、数量与价格；关闭软件后自动重新上锁。",
                     bg=theme.color("surface"), fg=theme.color("muted"),
                     font=theme.font("caption")).pack(side="left")
            Button(row, "立即上锁", command=self._disarm, variant="ghost",
                   size="sm").pack(side="right")
            return
        self._gate_title(holder, "实盘未解锁", "warning")
        tk.Label(holder, text="实盘会下真实订单、动用真实资金。请确认你已理解风险，"
                              "并先用模拟柜台跑通同样的流程。",
                 bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w", justify="left").pack(fill="x", pady=(2, 8))
        self.risk_var = tk.BooleanVar(value=False)
        Toggle(holder, "我已理解实盘风险，并确认账户与资金由本人负责",
               variable=self.risk_var, bg=theme.color("surface"),
               command=self._sync_unlock_state).pack(anchor="w")
        row = tk.Frame(holder, bg=theme.color("surface"))
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text="输入「%s」解锁" % UNLOCK_PHRASE, bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption")).pack(side="left")
        self.phrase_var = tk.StringVar()
        self.phrase_var.trace_add("write", lambda *_: self._sync_unlock_state())
        tk.Entry(row, textvariable=self.phrase_var, width=12, font=theme.font("body"),
                 bg=theme.color("surface_alt"), fg=theme.color("text"),
                 insertbackground=theme.color("text"), relief="flat",
                 highlightthickness=1, highlightbackground=theme.color("border"),
                 highlightcolor=theme.color("accent")).pack(side="left", padx=(8, 10), ipady=4)
        self.unlock_button = Button(row, "解锁实盘", command=self._arm,
                                    variant="danger", size="sm")
        self.unlock_button.pack(side="left")
        self._sync_unlock_state()

    def _gate_title(self, parent, text: str, tone: str) -> None:
        color = {"warning": theme.color("warning"), "success": theme.color("success"),
                 "text": theme.color("text")}.get(tone, theme.color("text"))
        tk.Label(parent, text=text, bg=theme.color("surface"), fg=color,
                 font=theme.font("h2"), anchor="w").pack(fill="x")

    def _sync_unlock_state(self) -> None:
        button = getattr(self, "unlock_button", None)
        if button is None:
            return
        ok = bool(self.risk_var.get()) and self.phrase_var.get().strip() == UNLOCK_PHRASE
        button.set_enabled(ok)

    def _arm(self) -> None:
        service = self.c.services.live_trade
        try:
            service.arm()
        except TradingError as error:
            self.error("无法解锁", str(error))
            return
        self._render_gate()
        self.toast("实盘已解锁，仅本次运行有效", kind="warning")
        self._refresh_all()

    def _disarm(self) -> None:
        service = self.c.services.live_trade
        if service.running:
            self.shell.confirm("还有实盘会话", "上锁前需要先停止当前实盘会话，继续吗？",
                               self._do_disarm, confirm_text="停止并上锁", danger=True)
            return
        self._do_disarm()

    def _do_disarm(self) -> None:
        service = self.c.services.live_trade
        if service.running:
            service.stop_session(reason="用户上锁")
        service.disarm()
        self._render_gate()
        self._refresh_all()

    # ---------------------------------------------------------- 连接

    def _build_connect(self, parent) -> None:
        card = Card(parent, padding=12)
        card.pack(fill="x", padx=theme.px(24), pady=(0, theme.px(10)))
        SectionHeader(card.body, "连接柜台",
                      "模拟柜台用于演练；QMT 直连需要本机已登录 miniQMT，"
                      "软件只保存目录与资金账号，不保存券商密码。").pack(fill="x")
        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x", pady=(theme.px(8), 4))
        tk.Label(row, text="柜台", bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption")).pack(side="left")
        self.gateway_var = tk.StringVar(value=GATEWAYS[0][0])
        self.gateway_combo = ttk.Combobox(row, textvariable=self.gateway_var, state="readonly",
                                          width=18, values=[label for label, _ in GATEWAYS],
                                          font=theme.font("body"))
        self.gateway_combo.pack(side="left", padx=(6, theme.px(12)))
        self.qmt_path_entry = LabeledEntry(row, "userdata_mini 目录", "", width=26)
        self.qmt_path_entry.pack(side="left", padx=(0, theme.px(8)))
        self.qmt_account_entry = LabeledEntry(row, "资金账号", "", width=13)
        self.qmt_account_entry.pack(side="left", padx=(0, theme.px(8)))
        self.qmt_type_var = tk.StringVar(value=QMT_ACCOUNT_TYPES[0][0])
        self.qmt_type_combo = ttk.Combobox(row, textvariable=self.qmt_type_var,
                                           state="readonly", width=12,
                                           values=[label for label, _ in QMT_ACCOUNT_TYPES],
                                           font=theme.font("body"))
        self.qmt_type_combo.pack(side="left")

        row2 = tk.Frame(card.body, bg=theme.color("surface"))
        row2.pack(fill="x", pady=(4, 0))
        tk.Label(row2, text="策略", bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption")).pack(side="left")
        self.strategy_var = tk.StringVar()
        self.strategy_combo = ttk.Combobox(row2, textvariable=self.strategy_var, state="readonly",
                                           width=20, font=theme.font("body"))
        self.strategy_combo.pack(side="left", padx=(6, theme.px(10)))
        self.symbols_entry = LabeledEntry(row2, "标的", "600519.SH", width=18)
        self.symbols_entry.pack(side="left", padx=(0, theme.px(8)))
        self.cash_entry = LabeledEntry(row2, "初始资金（模拟）", "", width=11)
        self.cash_entry.pack(side="left", padx=(0, theme.px(12)))
        self.connect_button = Button(row2, "连接", command=self._connect, variant="primary")
        self.connect_button.pack(side="left")
        self.disconnect_button = Button(row2, "断开", command=self._disconnect,
                                       variant="secondary")
        self.disconnect_button.pack(side="left", padx=(6, 0))
        self.estop_button = Button(row2, "紧急停止", command=self._emergency_stop,
                                   variant="danger")
        self.estop_button.pack(side="left", padx=(6, 0))

        self.connect_hint = tk.Label(card.body, text="", bg=theme.color("surface"),
                                     fg=theme.color("muted"), font=theme.font("caption"),
                                     anchor="w", justify="left", wraplength=theme.px(1100))
        self.connect_hint.pack(fill="x", pady=(theme.px(8), 0))

    def _connect(self) -> None:
        service = self.c.services.live_trade
        kind = dict(GATEWAYS).get(self.gateway_var.get(), "sim")
        symbols = [item.strip().upper() for item in
                   self.symbols_entry.get().replace("，", ",").split(",") if item.strip()]
        if not symbols:
            self.error("缺少标的", "请填写至少一个标的代码，例如 600519.SH。")
            return
        unknown = [s for s in symbols if self.c.instruments.get(s) is None]
        if unknown:
            self.error("标的不存在", "无法识别：%s" % "、".join(unknown),
                       hint="支持 A 股与场内 ETF，代码形如 600519.SH、510300.SH。")
            return
        if kind == "qmt":
            if not self.qmt_path_entry.get().strip():
                self.error("缺少 QMT 目录", "请填写 miniQMT 的 userdata_mini 目录。",
                           hint="在 QMT 安装目录下，通常形如 D:\\QMT\\userdata_mini。")
                return
            if not self.qmt_account_entry.get().strip():
                self.error("缺少资金账号", "请填写券商资金账号。")
                return
        strategy_key = self._strategy_labels.get(self.strategy_var.get(), "")
        try:
            cash = float(self.cash_entry.get() or 0) if kind == "sim" else None
        except ValueError:
            self.error("初始资金不合法", "初始资金必须是数字。")
            return
        try:
            info = service.start_session(
                gateway_kind=kind,
                qmt_path=self.qmt_path_entry.get().strip(),
                qmt_account=self.qmt_account_entry.get().strip(),
                qmt_account_type=dict(QMT_ACCOUNT_TYPES).get(self.qmt_type_var.get(), "STOCK"),
                strategy_key=strategy_key, symbols=symbols, initial_cash=cash,
                auto_trade=bool(self.auto_var.get()) if hasattr(self, "auto_var") else False,
            )
        except TradingError as error:
            self.error("连接失败", str(error))
            return
        except Exception as error:  # noqa: BLE001 - 柜台异常要原样告诉用户
            self.error("连接失败", str(error))
            return
        self.toast("柜台已连接" if kind == "qmt" else "模拟柜台已连接",
                   kind="success", title=info.get("status", ""))
        self._refresh_all()

    def _disconnect(self) -> None:
        service = self.c.services.live_trade
        if not service.running:
            self.toast("当前没有实盘会话")
            return
        self.shell.confirm("断开实盘", "断开前会先撤销所有未成交委托，确定吗？",
                           self._do_disconnect, confirm_text="断开", danger=True)

    def _do_disconnect(self) -> None:
        try:
            self.c.services.live_trade.stop_session(reason="用户断开")
        except TradingError as error:
            self.error("断开失败", str(error))
        self._refresh_all()

    def _emergency_stop(self) -> None:
        self.shell.confirm(
            "紧急停止", "将撤销全部未成交委托，并停止本次实盘会话。",
            self._do_emergency_stop, confirm_text="立即停止", danger=True,
            require_phrase="紧急停止")

    def _do_emergency_stop(self) -> None:
        try:
            self.c.services.live_trade.emergency_stop("人工紧急停止")
        except TradingError as error:
            self.error("紧急停止失败", str(error))
        else:
            self.toast("已撤销未成交委托并停止实盘会话", kind="warning", title="紧急停止")
        self._refresh_all()

    # ---------------------------------------------------------- 指标

    def _build_kpis(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24))
        self.kpi_equity = KpiCard(row, "总资产", "—")
        self.kpi_cash = KpiCard(row, "可用资金", "—")
        self.kpi_value = KpiCard(row, "持仓市值", "—")
        self.kpi_pnl = KpiCard(row, "浮动盈亏", "—")
        for card in (self.kpi_equity, self.kpi_cash, self.kpi_value, self.kpi_pnl):
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.px(10)))
        self.kpi_pnl.pack_configure(padx=0)

    # ---------------------------------------------------------- 下单与持仓

    def _build_order_and_positions(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), 0))

        left = Card(row, padding=12)
        left.configure(width=theme.px(300))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        SectionHeader(left.body, "手动下单", "限价单；风控、T+1 与费用与模拟盘一致。").pack(fill="x")
        tk.Label(left.body, text="标的", bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(fill="x", pady=(theme.px(8), 2))
        self.order_symbol_var = tk.StringVar()
        self.order_symbol_combo = ttk.Combobox(left.body, textvariable=self.order_symbol_var,
                                               state="readonly", font=theme.font("body"))
        self.order_symbol_combo.pack(fill="x")
        self.order_symbol_combo.bind("<<ComboboxSelected>>", lambda _e: self._fill_price())
        tk.Label(left.body, text="方向 / 数量", bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(fill="x", pady=(theme.px(8), 2))
        side_row = tk.Frame(left.body, bg=theme.color("surface"))
        side_row.pack(fill="x")
        self.side_var = tk.StringVar(value=SIDES[0][0])
        ttk.Combobox(side_row, textvariable=self.side_var, state="readonly", width=7,
                     values=[label for label, _ in SIDES],
                     font=theme.font("body")).pack(side="left")
        self.qty_entry = LabeledEntry(side_row, "数量", "100", width=8)
        self.qty_entry.pack(side="left", padx=(6, 0))
        tk.Label(left.body, text="价格", bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(fill="x", pady=(theme.px(8), 2))
        price_row = tk.Frame(left.body, bg=theme.color("surface"))
        price_row.pack(fill="x")
        self.price_entry = LabeledEntry(price_row, "价格", "", width=10)
        self.price_entry.pack(side="left")
        Button(price_row, "刷新现价", command=self._fill_price, variant="ghost",
               size="sm").pack(side="left", padx=(6, 0))
        self.submit_button = Button(left.body, "提交委托", command=self._submit,
                                    variant="primary", size="sm")
        self.submit_button.pack(fill="x", pady=(theme.px(10), 0))
        self.order_hint = tk.Label(left.body, text="", bg=theme.color("surface"),
                                   fg=theme.color("muted"), font=theme.font("caption"),
                                   anchor="w", justify="left", wraplength=theme.px(260))
        self.order_hint.pack(fill="x", pady=(6, 0))

        right = Card(row, padding=12)
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(10), 0))
        SectionHeader(right.body, "持仓", "以柜台回报为准；T+1 当日买入次日可卖。").pack(fill="x")
        self.positions = DataTable(right.body, [
            ("symbol", "标的", 92), ("qty", "数量", 62, "e"),
            ("available", "可卖", 62, "e"), ("cost", "成本", 74, "e"),
            ("last", "现价", 74, "e"), ("value", "市值", 86, "e"),
            ("pnl", "盈亏", 80, "e"), ("pct", "比例", 64, "e"),
        ], height=5)
        self.positions.pack(fill="both", expand=True, pady=(theme.px(6), 0))

    def _fill_price(self) -> None:
        symbol = self._symbol_of_label(self.order_symbol_var.get())
        if not symbol:
            return
        quote = self._quote_map().get(symbol)
        if quote and quote.get("price"):
            self.price_entry.set("%.2f" % float(quote["price"]))

    def _submit(self) -> None:
        symbol = self._symbol_of_label(self.order_symbol_var.get())
        if not symbol:
            self.error("没有选择标的", "请先在下单卡里选择标的。")
            return
        side = dict((label, value) for label, value in SIDES).get(self.side_var.get(), SIDE_BUY)
        try:
            qty = int(float(self.qty_entry.get() or 0))
            price = float(self.price_entry.get() or 0)
        except ValueError:
            self.error("数量或价格不合法", "数量与价格都必须是数字。")
            return
        if qty <= 0 or price <= 0:
            self.error("数量或价格不合法", "数量与价格都必须大于 0。")
            return
        label = "买入" if side == SIDE_BUY else "卖出"
        self.shell.confirm(
            "确认实盘委托",
            "%s %s %d 股，限价 %.2f，金额约 %.2f 元。\n提交后不可撤回，确认下单吗？"
            % (label, symbol, qty, price, qty * price),
            lambda: self._do_submit(symbol, side, qty, price),
            confirm_text="确认下单", danger=True)
        self._audit_ui_confirm(symbol, side, qty, price)

    def _audit_ui_confirm(self, symbol: str, side: str, qty: int, price: float) -> None:
        try:
            self.c.repos.audit.write("live", "confirm_order",
                                     detail="%s %s %d @ %.2f" % (side, symbol, qty, price))
        except Exception:  # noqa: BLE001 - 审计失败不阻断
            pass

    def _do_submit(self, symbol: str, side: str, qty: int, price: float) -> None:
        try:
            order = self.c.services.live_trade.place_order(symbol, side, qty, price=price)
        except TradingError as error:
            self.error("下单失败", str(error))
            return
        except Exception as error:  # noqa: BLE001
            self.error("下单失败", str(error))
            return
        self.toast("委托已提交：%s" % ORDER_STATUS.get(order.status, order.status),
                   kind="success" if order.status != "REJECTED" else "error")
        self._refresh_all()

    def _cancel_selected(self) -> None:
        row = self.orders.selected()
        if not row:
            self.toast("先选中一笔未成交委托", kind="warning")
            return
        status = row.get("raw_status") or ""
        if status not in ORDER_STATUS_OPEN:
            self.toast("该委托已终结，不能撤单", kind="warning")
            return
        order_id = row.get("order_id")
        self.shell.confirm("撤销委托", "撤销 %s 的未成交部分？" % row.get("symbol", ""),
                           lambda: self._do_cancel(order_id), confirm_text="撤单",
                           danger=False)

    def _do_cancel(self, order_id: str) -> None:
        try:
            ok = self.c.services.live_trade.cancel(order_id)
        except TradingError as error:
            self.error("撤单失败", str(error))
            return
        self.toast("撤单成功" if ok else "撤单未成功，请刷新委托确认", kind="success" if ok else "warning")
        self._refresh_all()

    # ---------------------------------------------------------- 委托与成交

    def _build_tables(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), 0))
        left = Card(row, padding=12)
        left.pack(side="left", fill="both", expand=True)
        head = SectionHeader(left.body, "委托", "被风控拒绝的委托也会留痕。")
        head.pack(fill="x")
        Button(head.actions, "撤销选中", command=self._cancel_selected, variant="secondary",
               size="sm").pack(side="right")
        self.orders = DataTable(left.body, [
            ("time", "时间", 96), ("symbol", "标的", 88),
            ("side", "方向", 44, "center"), ("qty", "数量", 56, "e"),
            ("price", "价格", 64, "e"), ("filled", "已成", 52, "e"),
            ("status", "状态", 52, "center"), ("reason", "说明", 120),
        ], height=4, on_double=lambda _e: self._cancel_selected())
        self.orders.pack(fill="both", expand=True, pady=(theme.px(6), 0))

        right = Card(row, padding=12)
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(10), 0))
        SectionHeader(right.body, "成交", "按成交编号去重，柜台重复回报不会重复计账。").pack(fill="x")
        self.trades = DataTable(right.body, [
            ("time", "时间", 96), ("symbol", "标的", 88),
            ("side", "方向", 44, "center"), ("qty", "数量", 56, "e"),
            ("price", "价格", 64, "e"), ("fees", "费用", 62, "e"),
            ("trade_id", "成交编号", 120),
        ], height=4)
        self.trades.pack(fill="both", expand=True, pady=(theme.px(6), 0))

    # ---------------------------------------------------------- 自动交易

    def _build_auto(self, parent) -> None:
        card = Card(parent, padding=12)
        card.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), theme.px(8)))
        SectionHeader(card.body, "自动交易",
                      "只跑一次：交易日的计划时间用日线信号下单，不循环、不追单。").pack(fill="x")
        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x", pady=(theme.px(8), 0))
        self.auto_var = tk.BooleanVar(value=bool(self.c.config.live.auto_trade))
        Toggle(row, "连接后按计划时间自动跑一轮策略", variable=self.auto_var,
               bg=theme.color("surface"),
               command=self._toggle_auto).pack(side="left")
        self.auto_run_button = Button(row, "现在跑一轮", command=self._run_once,
                                      variant="secondary", size="sm")
        self.auto_run_button.pack(side="left", padx=(theme.px(12), 0))
        self.auto_label = tk.Label(card.body, text="", bg=theme.color("surface"),
                                   fg=theme.color("muted"), font=theme.font("caption"),
                                   anchor="w", justify="left", wraplength=theme.px(1100))
        self.auto_label.pack(fill="x", pady=(theme.px(6), 0))

    def _toggle_auto(self) -> None:
        value = bool(self.auto_var.get())
        self.c.config.live.auto_trade = value
        self.c.save_config()
        service = self.c.services.live_trade
        if service.running:
            self.toast("自动交易开关将在下次连接时生效；当前会话不变", kind="warning")
        else:
            self.toast("自动交易已%s" % ("开启" if value else "关闭"))

    def _run_once(self) -> None:
        service = self.c.services.live_trade
        if not service.running:
            self.error("还没有连接", "请先连接柜台，再运行策略。")
            return
        self.shell.confirm(
            "运行一轮策略",
            "会按当前策略与标的生成信号，并通过实盘风控下单。确认继续吗？",
            self._do_run_once, confirm_text="运行", danger=True)

    def _do_run_once(self) -> None:
        try:
            result = self.c.services.live_trade.run_strategy_once(force=True)
        except TradingError as error:
            self.error("策略运行失败", str(error))
            return
        except Exception as error:  # noqa: BLE001
            self.error("策略运行失败", str(error))
            return
        if not result.get("ok"):
            self.error("策略没有跑通", str(result.get("error") or result))
        else:
            self.toast("策略已跑完一轮，可到委托列表查看结果", kind="success")
        self._refresh_all()

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        self._refresh_strategies()
        self._refresh_symbols()
        self._refresh_config()
        self._refresh_status()
        self._refresh_account()
        self._refresh_tables()
        if self.c.services.live_trade.running:
            self._schedule_poll()

    def _refresh_config(self) -> None:
        live = self.c.config.live
        if not self.qmt_path_entry.get():
            self.qmt_path_entry.set(live.qmt_path)
        if not self.qmt_account_entry.get():
            self.qmt_account_entry.set(live.qmt_account)
        if not self.cash_entry.get():
            self.cash_entry.set("%.0f" % self.c.config.trading.initial_cash)
        labels = [label for label, kind in QMT_ACCOUNT_TYPES
                  if kind == live.qmt_account_type]
        if labels:
            self.qmt_type_var.set(labels[0])
        gateway_labels = [label for label, kind in GATEWAYS if kind == live.gateway_kind]
        if gateway_labels and not self.c.services.live_trade.running:
            self.gateway_var.set(gateway_labels[0])
        self._render_gate()

    def _refresh_strategies(self) -> None:
        self._strategy_labels: Dict[str, str] = {}
        labels = []
        for key in self.c.strategies.keys():
            meta = self.c.strategies.meta(key)
            label = "%s · %s" % (meta.name, meta.category)
            labels.append(label)
            self._strategy_labels[label] = key
        self.strategy_combo.configure(values=labels)
        if not self.strategy_var.get() and labels:
            self.strategy_var.set(labels[0])

    def _quote_map(self) -> Dict[str, dict]:
        try:
            return {row["symbol"]: row for row in self.c.services.market.quotes()}
        except Exception:  # noqa: BLE001
            return {}

    def _refresh_symbols(self) -> None:
        quotes = self._quote_map()
        rows = [(row["symbol"], row) for row in quotes.values() if row.get("has_data")]
        rows.sort(key=lambda item: item[0])
        self._order_symbols = {}
        labels: List[str] = []
        for symbol, row in rows:
            label = "%s %s" % (symbol, row.get("name", ""))
            labels.append(label)
            self._order_symbols[label] = symbol
        self.order_symbol_combo.configure(values=labels)
        if not self.order_symbol_var.get() and labels:
            self.order_symbol_var.set(labels[0])
            self._fill_price()

    def _symbol_of_label(self, label: str) -> Optional[str]:
        return getattr(self, "_order_symbols", {}).get(label)

    def _refresh_status(self) -> None:
        service = self.c.services.live_trade
        info = service.status_dict()
        state = info["status"]
        text = STATUS_LABELS.get(state, state)
        if info["armed"]:
            text += " · 已解锁"
        self.status_label.configure(text=text)
        color = {"connected": "success", "running": "success", "error": "danger",
                 "connecting": "warning", "idle": "muted", "disabled": "muted"}.get(state, "muted")
        self.status_label.configure(fg=theme.color(color))
        if info.get("error"):
            self.connect_hint.configure(text="最近错误：%s" % info["error"],
                                        fg=theme.color("danger"))
        elif info["connected"]:
            quote = "实时行情" if info["quote_mode"] == "realtime" else "最近收盘价"
            self.connect_hint.configure(
                text="会话 %s · %s · 行情来源 %s · 自动交易 %s"
                     % (info["session_id"] or "—", info["gateway"] or "—", quote,
                        "开" if info["auto_trade"] else "关"),
                fg=theme.color("muted"))
        else:
            self.connect_hint.configure(
                text="实盘默认关闭；先在设置中开启，再解锁并连接。QMT 需要本机 miniQMT 已登录。",
                fg=theme.color("muted"))
        running = info["running"]
        if running:
            key = info.get("strategy_key") or ""
            for label, value in getattr(self, "_strategy_labels", {}).items():
                if value == key:
                    self.strategy_var.set(label)
                    break
            symbols = info.get("symbols") or []
            if symbols:
                self.symbols_entry.set(",".join(symbols))
            if info.get("initial_cash"):
                self.cash_entry.set("%.0f" % float(info["initial_cash"]))
            self.symbols_entry.entry.configure(state="disabled")
            self.cash_entry.entry.configure(state="disabled")
        else:
            self.symbols_entry.entry.configure(state="normal")
            self.cash_entry.entry.configure(state="normal")
        self.connect_button.set_enabled(not running and info["armed"])
        self.disconnect_button.set_enabled(running)
        self.estop_button.set_enabled(running)
        self.submit_button.set_enabled(running and info["armed"])
        self.auto_run_button.set_enabled(running)
        self.gateway_combo.configure(state="disabled" if running else "readonly")
        if hasattr(self, "auto_var"):
            self.auto_label.configure(
                text="计划时间 %s（交易日） · 自动交易状态：%s"
                     % (self.c.config.live.run_time,
                        "已开启" if self.auto_var.get() else "关闭")
                     + (" · 今日已执行" if info.get("auto_done") else ""))
        if self.c.config.live.allow_market_orders:
            self.order_hint.configure(text="市价单已开启：仍会转成保护价限价单再发送。")
        else:
            self.order_hint.configure(text="只支持限价单；市价单需在设置中显式开启。")

    def _refresh_account(self) -> None:
        service = self.c.services.live_trade
        snapshot = service.account_snapshot()
        info = service.status_dict()
        if not info["running"] and info["status"] == "idle":
            for card in (self.kpi_equity, self.kpi_cash, self.kpi_value, self.kpi_pnl):
                card.set("—")
            return
        self.kpi_equity.set(fmt.money(snapshot["equity"]),
                            "%s 回撤" % fmt.pct(snapshot["drawdown"], 2, signed=True),
                            tone="muted")
        self.kpi_cash.set(fmt.money(snapshot["cash"]), "冻结 %s" % fmt.money(snapshot["frozen"]))
        self.kpi_value.set(fmt.money(snapshot["market_value"]), "持仓按现价计")
        pnl = snapshot["unrealized_pnl"]
        tone = "up" if pnl > 0 else ("down" if pnl < 0 else "muted")
        self.kpi_pnl.set(fmt.money(pnl, sign=True), "浮动盈亏", tone=tone)

    def _refresh_tables(self) -> None:
        service = self.c.services.live_trade
        positions = []
        for row in service.positions():
            positions.append({
                "symbol": row["symbol"], "qty": row["qty"],
                "available": row["available"], "cost": "%.3f" % row["avg_cost"],
                "last": "%.2f" % row["last_price"], "value": fmt.money(row["value"]),
                "pnl": fmt.money(row["pnl"], sign=True),
                "pct": fmt.pct(row["pnl_pct"], 2, signed=True),
                "tone": row["pnl"],
            })
        self.positions.set_rows_colored(positions, "tone")
        orders = []
        for row in service.orders(80):
            orders.append({
                "order_id": row["order_id"], "raw_status": row["status"],
                "time": fmt.date_text(row["time"], with_time=True),
                "symbol": row["symbol"],
                "side": "买入" if row["side"] == SIDE_BUY else "卖出",
                "qty": row["qty"], "price": "%.2f" % (row["price"] or 0),
                "filled": row["filled_qty"],
                "status": ORDER_STATUS.get(row["status"], row["status"]),
                "reason": fmt.truncate(row["reason"] or "", 26),
                "tone": (1 if row["status"] == "FILLED" else
                         (-1 if row["status"] == "REJECTED" else 0)),
            })
        self.orders.set_rows_colored(orders, "tone")
        trades = []
        for row in service.trades(80):
            trades.append({
                "time": fmt.date_text(row["time"], with_time=True),
                "symbol": row["symbol"],
                "side": "买入" if row["side"] == SIDE_BUY else "卖出",
                "qty": row["qty"], "price": "%.2f" % row["price"],
                "fees": "%.2f" % row["fees"], "trade_id": row["trade_id"],
            })
        self.trades.set_rows(trades)

    # ---------------------------------------------------------- 轮询与事件

    def _schedule_poll(self) -> None:
        if self._poll_job is not None:
            return
        self._poll_job = self.after(3000, self._poll)

    def _poll(self) -> None:
        self._poll_job = None
        service = self.c.services.live_trade
        if not service.running:
            return
        try:
            service.sync_now()
        except Exception:  # noqa: BLE001 - 轮询失败不打断页面
            pass
        self._refresh_status()
        self._refresh_account()
        self._refresh_tables()
        self._schedule_poll()

    def on_hide(self) -> None:
        for job in (self._poll_job,):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._poll_job = None

    def on_event(self, topic: str, payload: dict) -> None:
        if topic not in ("gateway.connected", "gateway.disconnected", "gateway.error",
                         "order.updated", "account.updated", "live_trade.started",
                         "live_trade.stopped", "auto_trade.triggered"):
            return
        if self._refresh_job is not None:
            return
        self._refresh_job = self.after(150, self._refresh_after_event)

    def _refresh_after_event(self) -> None:
        self._refresh_job = None
        self._refresh_status()
        self._refresh_account()
        self._refresh_tables()
        if self.c.services.live_trade.running:
            self._schedule_poll()
