"""风控管理器：按顺序执行规则，维护熔断状态与下单频率。"""

from __future__ import annotations

import threading
from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Sequence

from ..core.events import RISK_STATE_CHANGED, RISK_TRIGGERED
from ..core.logging import get_logger
from ..core.types import AccountSnapshot, Instrument, OrderRequest
from .rules import (
    ALL_RULES, RULE_IDS, RiskContext, RiskDecision, RiskLimits, RiskRule,
)

LOGGER = get_logger("risk.manager")


class RiskManager:
    def __init__(self, limits: Optional[RiskLimits] = None,
                 enabled_rules: Optional[Sequence[str]] = None,
                 rules: Optional[Sequence[RiskRule]] = None,
                 bus=None) -> None:
        self.limits = limits or RiskLimits()
        enabled = list(enabled_rules) if enabled_rules is not None else list(RULE_IDS)
        self.rules: List[RiskRule] = [rule for rule in (rules or ALL_RULES)
                                      if rule.rule_id in enabled or not enabled]
        self.bus = bus
        self.kill_switch = False
        self.kill_reason = ""
        self._lock = threading.RLock()
        self._order_times: Deque[datetime] = deque()
        self._rejections: Counter = Counter()
        self._rule_hits: Counter = Counter()
        self._checks = 0
        self._last: RiskDecision = RiskDecision(True, "none", "", "warn")
        self._state: Dict[str, Any] = {
            "equity": 0.0, "drawdown": 0.0, "day_pnl_pct": 0.0,
            "gross_exposure": 0.0, "positions": 0,
        }
        self._last_snapshot: Optional[AccountSnapshot] = None

    # ---------------------------------------------------------- 主流程

    def pre_trade(self, request: OrderRequest, price: float,
                  snapshot: AccountSnapshot, instrument: Optional[Instrument] = None,
                  now: Optional[datetime] = None,
                  is_open_session: bool = True) -> RiskDecision:
        moment = now or snapshot.dt
        with self._lock:
            self._checks += 1
            if self.kill_switch:
                decision = RiskDecision(False, "kill_switch",
                                        "风控熔断已开启：%s" % (self.kill_reason or "人工停止"))
                self._record(decision)
                return decision
            self._prune_orders(moment)
            ctx = self.build_context(request, price, snapshot, instrument,
                                     now=moment, is_open_session=is_open_session)
            for rule in self.rules:
                decision = rule.check(ctx, self.limits)
                if decision.rejected:
                    self._record(decision)
                    return decision
            self._last = RiskDecision(True, "passed", "", "warn")
            self._order_times.append(moment)
            return self._last

    def build_context(self, request: OrderRequest, price: float,
                      snapshot: AccountSnapshot, instrument: Optional[Instrument] = None,
                      now: Optional[datetime] = None,
                      is_open_session: bool = True) -> RiskContext:
        position_value = 0.0
        position_qty = 0
        for position in snapshot.positions:
            if position.symbol == request.symbol:
                position_qty = position.qty
                position_value = position.market_value
                break
        return RiskContext(
            request=request, price=float(price), equity=snapshot.equity,
            cash=snapshot.cash, instrument=instrument, position_qty=position_qty,
            position_value=position_value, gross_value=snapshot.market_value,
            day_pnl_pct=snapshot.day_pnl / snapshot.equity if snapshot.equity else 0.0,
            drawdown=snapshot.drawdown,
            orders_last_minute=len(self._order_times),
            kill_switch=self.kill_switch, is_open_session=is_open_session,
            now=now or snapshot.dt,
        )

    def on_snapshot(self, snapshot: AccountSnapshot) -> RiskDecision:
        """每根 bar 更新一次；回撤触发停机线时自动熔断。"""
        with self._lock:
            self._last_snapshot = snapshot
            self._state.update({
                "equity": snapshot.equity,
                "drawdown": snapshot.drawdown,
                "day_pnl_pct": (snapshot.day_pnl / snapshot.equity) if snapshot.equity else 0.0,
                "gross_exposure": snapshot.exposure,
                "positions": len(snapshot.positions),
            })
            if snapshot.drawdown >= self.limits.max_drawdown_halt and not self.kill_switch:
                self._trip("回撤 %.1f%% 达到停机线" % (snapshot.drawdown * 100),
                           publish=True)
                return RiskDecision(False, "drawdown",
                                    self.kill_reason, "reject")
        return RiskDecision(True, "ok")

    # ---------------------------------------------------------- 状态

    def _record(self, decision: RiskDecision) -> None:
        self._last = decision
        self._rejections[decision.rule_id] += 1
        self._rule_hits[decision.rule_id] += 1
        LOGGER.info("风控拒绝 %s：%s", decision.rule_id, decision.message)
        if self.bus is not None:
            self.bus.emit(RISK_TRIGGERED, rule_id=decision.rule_id,
                          message=decision.message, severity=decision.severity)

    def _prune_orders(self, moment: datetime) -> None:
        cutoff = moment - timedelta(minutes=1)
        while self._order_times and self._order_times[0] < cutoff:
            self._order_times.popleft()

    def _trip(self, reason: str, publish: bool = True) -> None:
        self.kill_switch = True
        self.kill_reason = reason
        LOGGER.warning("风控熔断：%s", reason)
        if publish and self.bus is not None:
            self.bus.emit(RISK_STATE_CHANGED, kill_switch=True, reason=reason)

    def trip_kill_switch(self, reason: str = "人工停止") -> None:
        with self._lock:
            self._trip(reason)

    def reset_kill_switch(self) -> None:
        with self._lock:
            self.kill_switch = False
            self.kill_reason = ""
            if self.bus is not None:
                self.bus.emit(RISK_STATE_CHANGED, kill_switch=False, reason="")

    def reset_day(self) -> None:
        with self._lock:
            self._order_times.clear()

    def set_limits(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if not hasattr(self.limits, key):
                    continue
                try:
                    setattr(self.limits, key, float(value))
                except (TypeError, ValueError):
                    continue
            if self.bus is not None:
                self.bus.emit(RISK_STATE_CHANGED, kill_switch=self.kill_switch,
                              reason=self.kill_reason)

    def set_enabled(self, rule_ids: Sequence[str]) -> None:
        with self._lock:
            selected = set(rule_ids)
            self.rules = [rule for rule in ALL_RULES if rule.rule_id in selected]

    def state(self) -> Dict[str, Any]:
        with self._lock:
            data = dict(self._state)
            data.update({
                "kill_switch": self.kill_switch,
                "kill_reason": self.kill_reason,
                "orders_last_minute": len(self._order_times),
                "checks": self._checks,
                "rejections": dict(self._rejections),
                "rule_hits": dict(self._rule_hits),
                "last": self._last.to_dict(),
                "limits": self.limits.__dict__.copy(),
                "enabled": [rule.rule_id for rule in self.rules],
                "rule_names": {rule.rule_id: rule.name for rule in ALL_RULES},
            })
            return data

    def reset(self) -> None:
        with self._lock:
            self.kill_switch = False
            self.kill_reason = ""
            self._order_times.clear()
            self._rejections.clear()
            self._rule_hits.clear()
            self._checks = 0
            self._last = RiskDecision(True, "none", "", "warn")
            self._state = {"equity": 0.0, "drawdown": 0.0, "day_pnl_pct": 0.0,
                           "gross_exposure": 0.0, "positions": 0}
