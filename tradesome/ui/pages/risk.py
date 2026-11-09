"""风控页：把"能不能下单"的规则摊开给人看，并且允许调整阈值。"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from ...risk.rules import RULE_IDS, RiskLimits
from ..widgets import Button, Card, DataTable, KpiCard, LabeledEntry, SectionHeader, Toggle
from .. import formatting as fmt
from .base import BasePage

RULE_TEXT = {
    "single_trade": ("单笔最大仓位", "一笔订单最多占组合权益的比例"),
    "single_symbol": ("单标的仓位", "同一只标的合计不超过的比例"),
    "gross_exposure": ("总仓位上限", "所有持仓市值合计上限，留出现金"),
    "daily_loss": ("当日亏损", "达到停开仓线只平不开，达到平仓线强制减仓"),
    "drawdown": ("回撤保护", "回撤到降档线减半仓，到停机线熔断"),
    "order_rate": ("下单频率", "每分钟最多提交的订单数，防止策略失控"),
    "price_band": ("价格偏离", "限价与现价的最大偏离，防止乌龙指"),
    "session": ("交易时段", "只在连续竞价时段允许下单"),
}
PERCENT_FIELDS = [
    ("single_trade_pct", "单笔最大仓位 %"),
    ("single_symbol_pct", "单标的仓位 %"),
    ("max_gross_exposure", "总仓位上限 %"),
    ("daily_loss_halt", "当日停开仓 %"),
    ("daily_loss_close", "当日强制平仓 %"),
    ("max_drawdown_reduce", "回撤降档 %"),
    ("max_drawdown_halt", "回撤熔断 %"),
    ("max_price_deviation_pct", "价格偏离 %"),
]


class RiskPage(BasePage):
    title = "风控"
    subtitle = "八条规则在每次下单前依次检查；被拒的订单会留下原因与规则编号。"

    help_steps = [
        '8 条规则在每次下单前依次检查。',
        '按百分比填写阈值，改完点「保存阈值」。',
        '全局熔断会暂停所有开仓；恢复前先查拒单记录。',
    ]

    def tour_targets(self) -> dict:
        return {}

    def build(self) -> None:
        self._entries = {}
        self._rule_vars = {}
        header = self.page_header()
        Button(header, "恢复默认", command=self._reset, variant="ghost",
               size="sm").pack(side="right", padx=(6, 0))
        Button(header, "保存阈值", command=self._save, variant="primary",
               size="sm").pack(side="right")

        body = self.scroll_body()
        self._build_kpis(body)
        middle = tk.Frame(body, bg=theme.color("bg"))
        middle.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), 0))
        self._build_limits(middle)
        self._build_rules(middle)
        self._build_footer(body)

    def _build_kpis(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24))
        self.kpi_kill = KpiCard(row, "熔断状态", "—")
        self.kpi_dd = KpiCard(row, "当前回撤", "—")
        self.kpi_day = KpiCard(row, "当日盈亏", "—")
        self.kpi_reject = KpiCard(row, "今日拒单", "—")
        for card in (self.kpi_kill, self.kpi_dd, self.kpi_day, self.kpi_reject):
            card.pack(side="left", fill="x", expand=True, padx=(0, theme.px(10)))
        self.kpi_reject.pack_configure(padx=0)

    def _build_limits(self, parent) -> None:
        card = Card(parent, padding=14)
        card.configure(width=theme.px(560))
        card.pack(side="left", fill="both")
        SectionHeader(card.body, "阈值",
                      "按百分比填写，例如 25 表示 25%。修改后点右上角保存。").pack(fill="x")
        grid = tk.Frame(card.body, bg=theme.color("surface"))
        grid.pack(fill="x", pady=(theme.px(10), 0))
        for index, (field, label) in enumerate(PERCENT_FIELDS):
            grid.grid_columnconfigure(index % 2, weight=1, uniform="limit")
            holder = tk.Frame(grid, bg=theme.color("surface"))
            holder.grid(row=index // 2, column=index % 2, sticky="ew",
                        padx=(0, theme.px(12) if index % 2 == 0 else 0),
                        pady=(0, theme.px(8)))
            entry = LabeledEntry(holder, label, "", width=10)
            entry.pack(fill="x")
            self._entries[field] = entry
        rate = LabeledEntry(card.body, "每分钟最多订单（笔）", "", width=10)
        rate.pack(fill="x", pady=(0, theme.px(8)))
        self._entries["max_orders_per_minute"] = rate
        self.session_var = tk.BooleanVar(value=False)
        Toggle(card.body, "只在交易时段允许下单（模拟盘回放时通常关闭）",
               self.session_var).pack(anchor="w")

    def _build_rules(self, parent) -> None:
        card = Card(parent, padding=14)
        card.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))
        SectionHeader(card.body, "启用的规则",
                      "取消勾选即关闭该条检查；关闭前请想清楚代价。").pack(fill="x")
        holder = tk.Frame(card.body, bg=theme.color("surface"))
        holder.pack(fill="both", expand=True, pady=(theme.px(8), 0))
        for rule_id in RULE_IDS:
            title, _desc = RULE_TEXT.get(rule_id, (rule_id, ""))
            var = tk.BooleanVar(value=True)
            Toggle(holder, title, var).pack(anchor="w",
                                            pady=(theme.px(2), theme.px(4)))
            self._rule_vars[rule_id] = var
        self.kill_var = tk.BooleanVar(value=False)
        Toggle(holder, "全局熔断（暂停全部开仓）", self.kill_var).pack(
            anchor="w", pady=(theme.px(10), 0))

    def _build_footer(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.color("bg"))
        row.pack(fill="x", padx=theme.px(24), pady=(theme.px(10), theme.px(16)))
        card = Card(row, padding=14)
        card.pack(side="left", fill="both", expand=True)
        SectionHeader(card.body, "熔断开关",
                      "熔断只阻止新开仓；已有持仓仍可通过卖出降低风险。").pack(fill="x")
        self.kill_status = tk.Label(card.body, text="", bg=theme.color("surface"),
                                    fg=theme.color("muted"), font=theme.font("caption"),
                                    anchor="w", justify="left")
        self.kill_status.pack(fill="x", pady=(theme.px(6), theme.px(8)))
        buttons = tk.Frame(card.body, bg=theme.color("surface"))
        buttons.pack(fill="x")
        Button(buttons, "立即熔断", command=self._trip, variant="danger",
               size="sm").pack(side="left")
        Button(buttons, "解除熔断", command=self._reset_kill, variant="secondary",
               size="sm").pack(side="left", padx=(6, 0))
        Button(buttons, "查看拒单记录", command=self._show_rejections,
               variant="ghost", size="sm").pack(side="left", padx=(6, 0))

        tips = Card(row, padding=14)
        tips.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))
        SectionHeader(tips.body, "为什么是这些数字").pack(fill="x")
        tk.Label(tips.body,
                 text=("单笔 25% 是界面默认的「仓位上限」，不是「单笔风险」。真正的单笔风险"
                       "由策略止损决定：仓位 × 止损幅度，建议控制在总权益的 1%–2%。\n"
                       "总仓位留 20% 现金，是为了在极端行情里还有腾挪空间；"
                       "回撤降档与熔断，是为了在策略失效时强制你停下来。"),
                 bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("caption"), justify="left", anchor="w",
                 wraplength=theme.px(460)).pack(fill="x", pady=(theme.px(8), 0))

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        risk = self.c.config.risk
        percent_keys = {field for field, _label in PERCENT_FIELDS}
        for field, entry in self._entries.items():
            value = getattr(risk, field, 0)
            if field in percent_keys:
                entry.set("%.1f" % (float(value) * 100.0))
            else:
                entry.set(str(value))
        enabled = set(risk.enabled_rules)
        for rule_id, var in self._rule_vars.items():
            var.set(rule_id in enabled)
        self.session_var.set(bool(getattr(risk, "enforce_session", False)))
        state = self.c.services.live.risk_state()
        kill = bool(state.get("kill_switch") or risk.kill_switch)
        self.kill_var.set(kill)
        self.kpi_kill.set("已熔断" if kill else "正常",
                          delta="只阻止开仓" if kill else "八条规则生效",
                          tone="danger" if kill else "success")
        drawdown = float(state.get("drawdown") or 0.0)
        self.kpi_dd.set(fmt.pct(drawdown), tone="warning" if drawdown > 0.1 else "muted")
        day_pnl = float(state.get("day_pnl_pct") or 0.0)
        self.kpi_day.set(fmt.pct(day_pnl, signed=True),
                         tone="up" if day_pnl > 0 else ("down" if day_pnl < 0 else "muted"))
        rejections = int(state.get("rejections") or 0)
        self.kpi_reject.set(str(rejections),
                            delta="运行中" if state.get("running") else "未运行模拟盘")
        self.kill_status.configure(
            text=("当前由「%s」触发。" % state.get("kill_reason")) if kill
            else "当前未熔断。手动熔断会立即阻止所有新开仓。")

    # ---------------------------------------------------------- 保存

    def _save(self) -> None:
        risk = self.c.config.risk
        updates = {}
        try:
            for field, entry in self._entries.items():
                raw = entry.get().replace("%", "").strip()
                value = float(raw)
                if field == "max_orders_per_minute":
                    if value < 1:
                        raise ValueError("每分钟订单数至少为 1")
                    updates[field] = int(value)
                else:
                    if value < 0 or value > 100:
                        raise ValueError("百分比应在 0 ~ 100 之间")
                    updates[field] = value / 100.0
        except ValueError as exc:
            self.error("阈值有误", str(exc))
            return
        if updates["daily_loss_close"] <= updates["daily_loss_halt"]:
            self.error("阈值有误", "当日强制平仓线必须高于停开仓线。")
            return
        if updates["max_drawdown_halt"] <= updates["max_drawdown_reduce"]:
            self.error("阈值有误", "回撤熔断线必须高于降档线。")
            return
        for field, value in updates.items():
            setattr(risk, field, value)
        risk.enabled_rules = [rule_id for rule_id, var in self._rule_vars.items()
                              if var.get()]
        risk.kill_switch = bool(self.kill_var.get())
        try:
            self.c.save_config()
            self.c.services.live.apply_risk_limits()
        except Exception as exc:  # noqa: BLE001
            self.error("保存失败", str(exc))
            return
        self.c.repos.audit.write("risk", "rule_change",
                                 detail="阈值更新：%s" % ", ".join(sorted(updates)))
        self.toast("风控阈值已保存", "success")
        self.refresh()

    def _reset(self) -> None:
        defaults = RiskLimits()
        risk = self.c.config.risk
        for field in ("single_trade_pct", "single_symbol_pct", "max_gross_exposure",
                      "daily_loss_halt", "daily_loss_close", "max_drawdown_reduce",
                      "max_drawdown_halt", "max_orders_per_minute",
                      "max_price_deviation_pct"):
            setattr(risk, field, getattr(defaults, field))
        risk.enabled_rules = list(RULE_IDS)
        try:
            self.c.save_config()
        except Exception:  # noqa: BLE001
            pass
        self.refresh()
        self.toast("已恢复默认阈值（尚未保存到运行中的引擎）", "info")

    # ---------------------------------------------------------- 熔断

    def _trip(self) -> None:
        self.shell.confirm(
            "确认熔断", "熔断后所有新开仓都会被拒绝，已有持仓不受影响。确定执行吗？",
            self._do_trip, confirm_text="立即熔断", danger=True)

    def _do_trip(self) -> None:
        self.c.config.risk.kill_switch = True
        try:
            self.c.save_config()
            self.c.services.live.trip_kill_switch("界面手动触发")
        except Exception as exc:  # noqa: BLE001
            self.error("操作失败", str(exc))
        self.c.repos.audit.write("risk", "trip", detail="界面手动触发熔断")
        self.refresh()
        self.toast("已熔断", "warning")

    def _reset_kill(self) -> None:
        self.c.config.risk.kill_switch = False
        try:
            self.c.save_config()
            self.c.services.live.reset_kill_switch()
        except Exception as exc:  # noqa: BLE001
            self.error("操作失败", str(exc))
        self.c.repos.audit.write("risk", "reset", detail="界面解除熔断")
        self.refresh()
        self.toast("已解除熔断", "success")

    def _show_rejections(self) -> None:
        rows = self.c.repos.audit.list_recent(limit=60, category="order")
        lines = ["%s  %s  %s" % (row["ts"], row["action"], row["detail"])
                 for row in rows if "reject" in (row.get("action") or "")]
        if not lines:
            self.toast("最近没有拒单记录", "info")
            return
        self.shell.show_error("最近拒单", "\n".join(lines[:20]))
