"""八道风控闸门。

每条规则只做一件事：拿到 RiskContext，返回 RiskDecision。
规则之间互不依赖，可以单独启停、单独测试、单独替换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ..core.types import SIDE_BUY, Instrument, OrderRequest

SEVERITY_REJECT = "reject"
SEVERITY_WARN = "warn"


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool = True
    rule_id: str = "none"
    message: str = ""
    severity: str = SEVERITY_WARN

    @property
    def rejected(self) -> bool:
        return not self.allowed

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "rule_id": self.rule_id,
                "message": self.message, "severity": self.severity}


ALLOW = RiskDecision(True)


@dataclass
class RiskContext:
    """一次下单前的完整快照，规则只读它。"""

    request: OrderRequest
    price: float
    equity: float
    cash: float
    instrument: Optional[Instrument] = None
    position_qty: int = 0
    position_value: float = 0.0
    gross_value: float = 0.0
    day_pnl_pct: float = 0.0
    drawdown: float = 0.0
    orders_last_minute: int = 0
    kill_switch: bool = False
    is_open_session: bool = True
    now: Optional[datetime] = None

    @property
    def side(self) -> str:
        return self.request.side

    @property
    def is_buy(self) -> bool:
        return self.request.side == SIDE_BUY

    @property
    def notional(self) -> float:
        return round(self.price * self.request.qty, 2)

    @property
    def symbol(self) -> str:
        return self.request.symbol

    @property
    def position_pct_after(self) -> float:
        if self.equity <= 0:
            return 1.0
        delta = self.notional if self.is_buy else -self.notional
        return (self.position_value + delta) / self.equity

    @property
    def gross_pct_after(self) -> float:
        if self.equity <= 0:
            return 1.0
        delta = self.notional if self.is_buy else -self.notional
        return (self.gross_value + delta) / self.equity


@dataclass
class RiskLimits:
    """规则阈值；默认值与 settings.json 的 risk 段一致。"""

    single_trade_pct: float = 0.25
    single_symbol_pct: float = 0.25
    max_gross_exposure: float = 0.80
    daily_loss_halt: float = 0.03
    daily_loss_close: float = 0.05
    max_drawdown_reduce: float = 0.15
    max_drawdown_halt: float = 0.20
    max_orders_per_minute: int = 20
    max_price_deviation_pct: float = 0.05
    enforce_session: bool = False

    @classmethod
    def from_config(cls, risk_config: Any) -> "RiskLimits":
        return cls(
            single_trade_pct=float(getattr(risk_config, "single_trade_pct", 0.25)),
            single_symbol_pct=float(getattr(risk_config, "single_symbol_pct", 0.25)),
            max_gross_exposure=float(getattr(risk_config, "max_gross_exposure", 0.80)),
            daily_loss_halt=float(getattr(risk_config, "daily_loss_halt", 0.03)),
            daily_loss_close=float(getattr(risk_config, "daily_loss_close", 0.05)),
            max_drawdown_reduce=float(getattr(risk_config, "max_drawdown_reduce", 0.15)),
            max_drawdown_halt=float(getattr(risk_config, "max_drawdown_halt", 0.20)),
            max_orders_per_minute=int(getattr(risk_config, "max_orders_per_minute", 20)),
            max_price_deviation_pct=float(getattr(risk_config, "max_price_deviation_pct", 0.05)),
        )


class RiskRule:
    rule_id = "base"
    name = "基础规则"
    description = ""

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        return ALLOW


class SingleTradeRule(RiskRule):
    rule_id = "single_trade"
    name = "单笔风险"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if not ctx.is_buy or ctx.equity <= 0:
            return ALLOW
        pct = ctx.notional / ctx.equity
        if pct > limits.single_trade_pct + 1e-9:
            return RiskDecision(False, self.rule_id,
                                "单笔下单占总资产 %.1f%%，超过上限 %.1f%%（可调小仓位或修改风控参数）"
                                % (pct * 100, limits.single_trade_pct * 100))
        return ALLOW


class SingleSymbolRule(RiskRule):
    rule_id = "single_symbol"
    name = "单标的集中度"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if not ctx.is_buy:
            return ALLOW
        pct = ctx.position_pct_after
        if pct > limits.single_symbol_pct + 1e-9:
            return RiskDecision(False, self.rule_id,
                                "%s 加仓后占总资产 %.1f%%，超过单标的上限 %.1f%%"
                                % (ctx.symbol, pct * 100, limits.single_symbol_pct * 100))
        return ALLOW


class GrossExposureRule(RiskRule):
    rule_id = "gross_exposure"
    name = "总仓位上限"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if not ctx.is_buy:
            return ALLOW
        pct = ctx.gross_pct_after
        if pct > limits.max_gross_exposure + 1e-9:
            return RiskDecision(False, self.rule_id,
                                "总仓位将达 %.1f%%，超过上限 %.1f%%（保留现金是长期活下来的前提）"
                                % (pct * 100, limits.max_gross_exposure * 100))
        return ALLOW


class DailyLossRule(RiskRule):
    rule_id = "daily_loss"
    name = "当日亏损熔断"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if ctx.day_pnl_pct <= -limits.daily_loss_close:
            if ctx.is_buy:
                return RiskDecision(False, self.rule_id,
                                    "当日亏损已达 %.1f%%，触发平仓线，禁止开新仓"
                                    % (ctx.day_pnl_pct * -100))
            return ALLOW
        if ctx.day_pnl_pct <= -limits.daily_loss_halt and ctx.is_buy:
            return RiskDecision(False, self.rule_id,
                                "当日亏损 %.1f%%，触发停止开仓线（%.1f%%）"
                                % (ctx.day_pnl_pct * -100, limits.daily_loss_halt * 100))
        return ALLOW


class DrawdownRule(RiskRule):
    rule_id = "drawdown"
    name = "回撤控制"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if ctx.drawdown >= limits.max_drawdown_halt:
            return RiskDecision(False, self.rule_id,
                                "历史回撤 %.1f%%，已触发停机线 %.1f%%，需人工确认后恢复"
                                % (ctx.drawdown * 100, limits.max_drawdown_halt * 100))
        if ctx.drawdown >= limits.max_drawdown_reduce and ctx.is_buy:
            return RiskDecision(False, self.rule_id,
                                "历史回撤 %.1f%%，超过降仓线 %.1f%%，暂停加仓"
                                % (ctx.drawdown * 100, limits.max_drawdown_reduce * 100))
        return ALLOW


class OrderRateRule(RiskRule):
    rule_id = "order_rate"
    name = "下单频率"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if ctx.orders_last_minute >= limits.max_orders_per_minute:
            return RiskDecision(False, self.rule_id,
                                "最近一分钟下单 %d 笔，超过上限 %d 笔（可能是策略逻辑异常）"
                                % (ctx.orders_last_minute, limits.max_orders_per_minute))
        return ALLOW


class PriceBandRule(RiskRule):
    rule_id = "price_band"
    name = "价格偏离"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        limit_price = ctx.request.limit_price
        if not limit_price or ctx.price <= 0:
            return ALLOW
        deviation = abs(limit_price / ctx.price - 1.0)
        if deviation > limits.max_price_deviation_pct + 1e-9:
            return RiskDecision(False, self.rule_id,
                                "限价 %.2f 偏离现价 %.2f 达 %.1f%%，疑似填错价格"
                                % (limit_price, ctx.price, deviation * 100))
        return ALLOW


class SessionRule(RiskRule):
    rule_id = "session"
    name = "交易时段"

    def check(self, ctx: RiskContext, limits: RiskLimits) -> RiskDecision:
        if not limits.enforce_session:
            return ALLOW
        if ctx.is_open_session:
            return ALLOW
        return RiskDecision(False, self.rule_id, "当前不在交易时段，订单已拒绝")


ALL_RULES: Sequence[RiskRule] = (
    SingleTradeRule(), SingleSymbolRule(), GrossExposureRule(), DailyLossRule(),
    DrawdownRule(), OrderRateRule(), PriceBandRule(), SessionRule(),
)

RULE_IDS = tuple(rule.rule_id for rule in ALL_RULES)
