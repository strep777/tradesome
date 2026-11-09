"""设置页：外观、交易默认值、数据与安全。所有改动写入 var/settings.json。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from .. import theme
from ...core.config import (AdvancedConfig, AppConfig, AppearanceConfig,
                            DataDefaults, SecurityDefaults, TradingDefaults)
from ..widgets import Button, Card, LabeledEntry, SectionHeader, Toggle
from .base import BasePage

THEME_LABELS = [("macOS 风格（推荐）", "macos"), ("深色", "dark"), ("浅色", "light")]
SCALES = ["0.9", "1.0", "1.1", "1.2", "1.3"]
START_PAGES = [("概览", "dashboard"), ("行情", "market"), ("策略库", "strategies"),
               ("实验室", "lab"), ("回测", "backtest"), ("模拟盘", "live"),
               ("实盘", "live_trade"), ("风控", "risk"), ("数据中心", "data"),
               ("帮助", "help")]
LIVE_GATEWAYS = [("模拟柜台", "sim"), ("迅投 QMT（miniQMT）", "qmt")]
QMT_ACCOUNT_TYPES = [("普通股票账户", "STOCK"), ("融资融券账户", "CREDIT")]
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


class SettingsPage(BasePage):
    title = "设置"
    subtitle = "改动会立即保存；外观类改动会重建界面，正在运行的回测不受影响。"

    help_steps = [
        '外观：主题、字号缩放与启动页。',
        '交易默认值决定回测与模拟盘的初始资金和费用。',
        '实盘默认关闭；启用后仍要每次到实盘页解锁。',
    ]

    def tour_targets(self) -> dict:
        return {}

    def build(self) -> None:
        self._entries = {}
        header = self.page_header()
        Button(header, "恢复默认", command=self._restore, variant="ghost",
               size="sm").pack(side="right", padx=(6, 0))
        Button(header, "保存设置", command=self._save, variant="primary",
               size="sm").pack(side="right")
        body = self.scroll_body()
        inner = tk.Frame(body, bg=theme.color("bg"))
        inner.pack(fill="both", expand=True, padx=theme.px(24), pady=(0, theme.px(20)))
        self._build_appearance(inner)
        self._build_trading(inner)
        self._build_data(inner)
        self._build_live(inner)
        self._build_security(inner)
        self._build_about(inner)

    # ---------------------------------------------------------- 分区

    def _build_appearance(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "外观", "默认 macOS 风格；深色适合长时间盯盘。字号缩放会同步表格与图表。").pack(fill="x")
        grid = tk.Frame(card.body, bg=theme.color("surface"))
        grid.pack(fill="x", pady=(theme.px(10), 0))
        grid.grid_columnconfigure(0, weight=1, uniform="app")
        grid.grid_columnconfigure(1, weight=1, uniform="app")
        grid.grid_columnconfigure(2, weight=1, uniform="app")
        self.theme_var = tk.StringVar(value=THEME_LABELS[0][0])
        self.scale_var = tk.StringVar(value="1.0")
        self.start_var = tk.StringVar(value="概览")
        self._combo_field(grid, 0, "主题", self.theme_var,
                          [label for label, _ in THEME_LABELS])
        self._combo_field(grid, 1, "字号缩放", self.scale_var, SCALES)
        self._combo_field(grid, 2, "启动页", self.start_var,
                          [label for label, _ in START_PAGES])
        self.tips_var = tk.BooleanVar(value=True)
        Toggle(card.body, "显示新手提示与引导", self.tips_var).pack(
            anchor="w", pady=(theme.px(10), 0))
        Button(card.body, "立即应用外观", command=self._apply_appearance,
               variant="secondary", size="sm").pack(anchor="w", pady=(theme.px(10), 0))
        Button(card.body, "重新观看新手引导", command=self._replay_guide,
               variant="ghost", size="sm").pack(anchor="w", pady=(theme.px(6), 0))

    def _replay_guide(self) -> None:
        try:
            self.c.repos.kv.set("onboarding.done", False)
        except Exception:  # noqa: BLE001
            pass
        self.shell.start_guide(0)

    def _build_trading(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "交易默认值",
                      "费用默认值按 A 股常见档位填写；不同券商请在交割单上核对。").pack(fill="x")
        grid = tk.Frame(card.body, bg=theme.color("surface"))
        grid.pack(fill="x", pady=(theme.px(10), 0))
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="trade")
        fields = [("initial_cash", "初始资金（元）"),
                  ("commission_rate", "佣金（万分之）"),
                  ("min_commission", "最低佣金（元）"),
                  ("stamp_tax_rate", "印花税（千分之，卖出）"),
                  ("transfer_fee_rate", "过户费（万分之）"),
                  ("slippage_bps", "滑点（bp）"),
                  ("max_volume_ratio", "成交量占比上限（%）")]
        for index, (field, label) in enumerate(fields):
            entry = LabeledEntry(grid, label, "", width=12)
            entry.grid(row=index // 3, column=index % 3, sticky="ew",
                       padx=(0, theme.px(10)), pady=(0, theme.px(8)))
            self._entries[field] = entry

    def _build_data(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        header = SectionHeader(
            card.body, "行情数据",
            "软件不内置演示行情。真实日线请在数据中心用 akshare 同步，或导入 CSV。")
        header.pack(fill="x")
        Button(header.actions, "打开数据中心",
               command=lambda: self.shell.navigate("data"),
               variant="secondary", size="sm").pack(side="right")
        tk.Label(card.body, text=("开发者仍可用 python -m tradesome --demo 临时生成合成行情；"
                                  "它只用于自检，不会在正常启动时写入数据。"),
                 bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("small"), anchor="w", justify="left",
                 wraplength=theme.px(520)).pack(fill="x", pady=(theme.px(8), 0))

    def _build_live(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "实盘",
                      "默认关闭；开启后每次启动仍需在实盘页重新解锁。券商账号密码由 QMT 客户端保管。").pack(fill="x")
        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x", pady=(theme.px(8), 0))
        self.live_enabled_var = tk.BooleanVar(value=False)
        Toggle(row, "启用实盘功能", self.live_enabled_var).pack(side="left")
        self.live_auto_var = tk.BooleanVar(value=False)
        Toggle(row, "连接后允许自动交易", self.live_auto_var).pack(side="left", padx=(theme.px(20), 0))
        self.live_market_var = tk.BooleanVar(value=False)
        Toggle(row, "允许市价单（转保护价限价）", self.live_market_var).pack(
            side="left", padx=(theme.px(20), 0))

        grid = tk.Frame(card.body, bg=theme.color("surface"))
        grid.pack(fill="x", pady=(theme.px(10), 0))
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="live")
        fields = [("qmt_path", "miniQMT userdata_mini 目录"), ("qmt_account", "资金账号"),
                  ("run_time", "自动交易时间 HH:MM"),
                  ("max_order_value", "单笔上限（元）"),
                  ("max_orders_per_day", "每日最多委托（笔）")]
        for index, (field, label) in enumerate(fields):
            entry = LabeledEntry(grid, label, "", width=18)
            entry.grid(row=index // 3, column=index % 3, sticky="ew",
                       padx=(0, theme.px(10)), pady=(0, theme.px(8)))
            self._entries["live_" + field] = entry
        self.live_gateway_var = tk.StringVar(value=LIVE_GATEWAYS[0][0])
        holder = tk.Frame(grid, bg=theme.color("surface"))
        holder.grid(row=1, column=2, sticky="ew", padx=(0, theme.px(10)),
                    pady=(0, theme.px(8)))
        tk.Label(holder, text="默认柜台", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        ttk.Combobox(holder, textvariable=self.live_gateway_var, state="readonly",
                     values=[label for label, _ in LIVE_GATEWAYS],
                     font=theme.font("body")).pack(fill="x", pady=(4, 0))
        self.live_qmt_type_var = tk.StringVar(value=QMT_ACCOUNT_TYPES[0][0])
        type_holder = tk.Frame(grid, bg=theme.color("surface"))
        type_holder.grid(row=2, column=0, sticky="ew", padx=(0, theme.px(10)),
                         pady=(0, theme.px(8)))
        tk.Label(type_holder, text="QMT 账号类型", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        ttk.Combobox(type_holder, textvariable=self.live_qmt_type_var, state="readonly",
                     values=[label for label, _ in QMT_ACCOUNT_TYPES],
                     font=theme.font("body")).pack(fill="x", pady=(4, 0))
        tk.Label(card.body, text="QMT 直连：软件在进程内调用迅投 xtquant（随 miniQMT 客户端提供，"
                                 "不需要 vnpy），只保存目录与资金账号；券商密码由客户端保管。",
                 bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w", justify="left",
                 wraplength=theme.px(820)).pack(fill="x", pady=(theme.px(6), 0))

    def _build_security(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "安全", "API 密钥保存在本地，优先使用 Windows DPAPI 加密。").pack(fill="x")
        self.vault_label = tk.Label(card.body, text="", bg=theme.color("surface"),
                                    fg=theme.color("muted"), font=theme.font("caption"),
                                    anchor="w", justify="left")
        self.vault_label.pack(fill="x", pady=(theme.px(8), theme.px(8)))
        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x")
        self.mask_var = tk.BooleanVar(value=True)
        Toggle(row, "界面与日志中的密钥打码", self.mask_var).pack(side="left")
        Button(row, "打开日志目录", command=self._open_logs, variant="ghost",
               size="sm").pack(side="right")

    def _build_about(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x")
        SectionHeader(card.body, "关于", "").pack(fill="x")
        from ... import RISK_DISCLAIMER, __version__
        tk.Label(card.body, text="TradeSome v%s · 本地量化交易研究台" % __version__,
                 bg=theme.color("surface"), fg=theme.color("text"),
                 font=theme.font("body"), anchor="w").pack(fill="x", pady=(theme.px(8), 4))
        tk.Label(card.body, text=RISK_DISCLAIMER, bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 justify="left", anchor="w", wraplength=theme.px(820)).pack(fill="x")
        Button(card.body, "清空回测历史", command=self._clear_backtests,
               variant="danger", size="sm").pack(anchor="w", pady=(theme.px(10), 0))

    def _combo_field(self, parent, column: int, label: str, variable,
                     values) -> None:
        holder = tk.Frame(parent, bg=theme.color("surface"))
        holder.grid(row=0, column=column, sticky="ew", padx=(0, theme.px(10)))
        tk.Label(holder, text=label, bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(fill="x")
        ttk.Combobox(holder, textvariable=variable, values=values, state="readonly",
                     font=theme.font("body")).pack(fill="x", pady=(4, 0))

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        appearance = self.c.config.appearance
        self.theme_var.set(_label_for(THEME_LABELS, appearance.theme))
        self.scale_var.set("%.1f" % appearance.font_scale)
        self.start_var.set(_label_for(START_PAGES, appearance.start_page))
        self.tips_var.set(bool(appearance.show_tips))

        trading = self.c.config.trading
        self._entries["initial_cash"].set("%.0f" % trading.initial_cash)
        self._entries["commission_rate"].set("%.3g" % (trading.commission_rate * 10000))
        self._entries["min_commission"].set("%.2f" % trading.min_commission)
        self._entries["stamp_tax_rate"].set("%.3g" % (trading.stamp_tax_rate * 1000))
        self._entries["transfer_fee_rate"].set("%.3g" % (trading.transfer_fee_rate * 10000))
        self._entries["slippage_bps"].set("%.3g" % trading.slippage_bps)
        self._entries["max_volume_ratio"].set("%.1f" % (trading.max_volume_ratio * 100))

        # 行情数据不再有可配置项：默认只读取真实行情

        live = self.c.config.live
        self.live_enabled_var.set(bool(live.enabled))
        self.live_auto_var.set(bool(live.auto_trade))
        self.live_market_var.set(bool(live.allow_market_orders))
        self.live_gateway_var.set(_label_for(LIVE_GATEWAYS, live.gateway_kind))
        self._entries["live_qmt_path"].set(live.qmt_path)
        self._entries["live_qmt_account"].set(live.qmt_account)
        self.live_qmt_type_var.set(_label_for(QMT_ACCOUNT_TYPES, live.qmt_account_type))
        self._entries["live_run_time"].set(live.run_time)
        self._entries["live_max_order_value"].set("%.0f" % live.max_order_value)
        self._entries["live_max_orders_per_day"].set(str(live.max_orders_per_day))

        vault = self.c.vault
        available = vault.available()
        weak = vault.weak_protection
        self.vault_label.configure(
            text=("加密方式：%s%s" % (vault.description(),
                                    "（弱保护，建议在 Windows 用户下使用）" if weak else "")),
            fg=theme.color("warning" if weak else "muted"))
        self.mask_var.set(bool(self.c.config.security.mask_secrets))

    # ---------------------------------------------------------- 保存

    def _save(self) -> None:
        try:
            trading = self.c.config.trading
            trading.initial_cash = _num(self._entries["initial_cash"], "初始资金", 1000)
            trading.commission_rate = _num(self._entries["commission_rate"],
                                           "佣金", 0) / 10000.0
            trading.min_commission = _num(self._entries["min_commission"], "最低佣金", 0)
            trading.stamp_tax_rate = _num(self._entries["stamp_tax_rate"],
                                          "印花税", 0) / 1000.0
            trading.transfer_fee_rate = _num(self._entries["transfer_fee_rate"],
                                             "过户费", 0) / 10000.0
            trading.slippage_bps = _num(self._entries["slippage_bps"], "滑点", 0)
            ratio = _num(self._entries["max_volume_ratio"], "成交量占比", 0)
            if ratio <= 0 or ratio > 100:
                raise ValueError("成交量占比应在 0 ~ 100 之间")
            trading.max_volume_ratio = ratio / 100.0

            data = self.c.config.data
            data.auto_generate_demo = False

            live = self.c.config.live
            live.enabled = bool(self.live_enabled_var.get())
            live.auto_trade = bool(self.live_auto_var.get())
            live.allow_market_orders = bool(self.live_market_var.get())
            live.gateway_kind = _value_for(LIVE_GATEWAYS, self.live_gateway_var.get())
            live.qmt_path = self._entries["live_qmt_path"].get().strip()
            live.qmt_account = self._entries["live_qmt_account"].get().strip()
            live.qmt_account_type = _value_for(QMT_ACCOUNT_TYPES,
                                               self.live_qmt_type_var.get())
            run_time = self._entries["live_run_time"].get().strip()
            _parse_time(run_time)
            live.run_time = run_time
            live.max_order_value = _num(self._entries["live_max_order_value"],
                                        "单笔上限", 1000)
            orders = int(_num(self._entries["live_max_orders_per_day"], "每日委托上限", 1))
            live.max_orders_per_day = orders
            live.reconnect_seconds = int(_num(self._entries["live_reconnect_seconds"],
                                              "重连间隔", 1))

            appearance = self.c.config.appearance
            appearance.theme = _value_for(THEME_LABELS, self.theme_var.get())
            appearance.font_scale = float(self.scale_var.get())
            appearance.start_page = _value_for(START_PAGES, self.start_var.get())
            appearance.show_tips = bool(self.tips_var.get())
            self.c.config.security.mask_secrets = bool(self.mask_var.get())
        except ValueError as exc:
            self.error("设置有误", str(exc))
            return
        try:
            self.c.save_config()
        except Exception as exc:  # noqa: BLE001
            self.error("保存失败", str(exc))
            return
        self.c.repos.audit.write("config", "change", detail="界面保存设置")
        self.toast("设置已保存", "success")

    def _apply_appearance(self) -> None:
        appearance = self.c.config.appearance
        target_theme = _value_for(THEME_LABELS, self.theme_var.get())
        target_scale = float(self.scale_var.get())
        if target_theme == appearance.theme and abs(target_scale - appearance.font_scale) < 1e-6:
            self.toast("外观没有变化", "info")
            return
        self.shell.set_appearance(theme_name=target_theme, font_scale=target_scale)

    def _restore(self) -> None:
        self.shell.confirm("恢复默认设置", "会重置外观、交易默认值、数据与安全选项，"
                           "风控阈值请在风控页单独调整。确定吗？",
                           self._do_restore, confirm_text="恢复默认", danger=True)

    def _do_restore(self) -> None:
        defaults = AppConfig()
        for section in ("appearance", "trading", "data", "security", "advanced"):
            current = getattr(self.c.config, section)
            source = getattr(defaults, section)
            for key, value in vars(source).items():
                setattr(current, key, value)
        try:
            self.c.save_config()
        except Exception as exc:  # noqa: BLE001
            self.error("保存失败", str(exc))
        self.refresh()
        self.toast("已恢复默认设置", "success")

    def _clear_backtests(self) -> None:
        self.shell.confirm("清空回测历史", "会删除数据库中的回测记录与交易明细，"
                           "已导出的报告文件不受影响。确定吗？",
                           self._do_clear, confirm_text="清空", danger=True)

    def _do_clear(self) -> None:
        try:
            self.c.repos.backtests.clear()
            self.toast("回测历史已清空", "success")
        except Exception as exc:  # noqa: BLE001
            self.error("清空失败", str(exc))

    def _open_logs(self) -> None:
        target = str(self.c.paths.logs)
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self.toast("日志目录：%s" % target, "info")


def _parse_time(text: str) -> None:
    try:
        hour, minute = [int(part) for part in text.split(":", 1)]
    except (ValueError, AttributeError):
        raise ValueError("自动交易时间格式应为 HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("自动交易时间格式应为 HH:MM")


def _num(entry: LabeledEntry, label: str, minimum: float) -> float:
    try:
        value = float(entry.get().replace(",", ""))
    except ValueError:
        raise ValueError("%s 需要填写数字" % label)
    if value < minimum:
        raise ValueError("%s 不能小于 %s" % (label, minimum))
    return value


def _label_for(pairs, value: str) -> str:
    for label, key in pairs:
        if key == value:
            return label
    return pairs[0][0]


def _value_for(pairs, label: str) -> str:
    for text, key in pairs:
        if text == label:
            return key
    return pairs[0][1]
