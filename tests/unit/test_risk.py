"""风控规则与管理器测试。"""

from __future__ import annotations

import unittest
from datetime import datetime

from tradesome.core.bus import EventBus
from tradesome.core.events import RISK_STATE_CHANGED, RISK_TRIGGERED
from tradesome.core.types import (SIDE_BUY, SIDE_SELL, AccountSnapshot,
                                  OrderRequest, Position)
from tradesome.risk import (ALL_RULES, RULE_IDS, RiskContext, RiskLimits,
                            RiskManager)
from tradesome.risk.rules import (DailyLossRule, DrawdownRule, GrossExposureRule,
                                  OrderRateRule, PriceBandRule, SessionRule,
                                  SingleSymbolRule, SingleTradeRule)

DT = datetime(2024, 3, 1, 10, 0)


def order_request(side=SIDE_BUY, qty=100, limit_price=0.0,
                  symbol="600519.SH") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=side, qty=qty,
                        limit_price=limit_price)


def snapshot(equity=100_000.0, cash=100_000.0, market_value=0.0,
             day_pnl=0.0, drawdown=0.0, positions=()) -> AccountSnapshot:
    return AccountSnapshot(dt=DT, cash=cash, frozen_cash=0.0,
                           market_value=market_value, equity=equity,
                           realized_pnl=0.0, unrealized_pnl=0.0,
                           day_pnl=day_pnl, peak_equity=equity,
                           drawdown=drawdown, positions=tuple(positions))


def context(request=None, price=10.0, equity=100_000.0, cash=100_000.0,
            position_qty=0, position_value=0.0, gross_value=0.0,
            day_pnl_pct=0.0, drawdown=0.0, orders=0,
            is_open_session=True) -> RiskContext:
    return RiskContext(
        request=request or order_request(), price=price, equity=equity, cash=cash,
        position_qty=position_qty, position_value=position_value,
        gross_value=gross_value, day_pnl_pct=day_pnl_pct,
        drawdown=drawdown, orders_last_minute=orders,
        is_open_session=is_open_session, now=DT,
    )


class RuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = RiskLimits()

    def test_rule_ids_are_unique_and_eight(self) -> None:
        self.assertEqual(len(RULE_IDS), 8)
        self.assertEqual(len(set(RULE_IDS)), 8)
        self.assertEqual(len(ALL_RULES), 8)

    def test_single_trade_rejects_oversized_buy(self) -> None:
        rule = SingleTradeRule()
        ctx = context(price=300.0, request=order_request(qty=100))  # 30%
        decision = rule.check(ctx, self.limits)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule_id, "single_trade")
        self.assertIn("30.0%", decision.message)
        self.assertTrue(rule.check(context(price=200.0), self.limits).allowed)
        self.assertTrue(rule.check(
            context(request=order_request(side=SIDE_SELL, qty=1000), price=300.0),
            self.limits).allowed)

    def test_single_symbol_counts_existing_position(self) -> None:
        rule = SingleSymbolRule()
        ctx = context(request=order_request(qty=100), price=10.0,
                      position_value=25_000.0)
        decision = rule.check(ctx, self.limits)
        self.assertFalse(decision.allowed)
        self.assertIn("加仓后", decision.message)
        self.assertTrue(rule.check(
            context(request=order_request(side=SIDE_SELL, qty=100), price=10.0,
                    position_value=25_000.0), self.limits).allowed)

    def test_gross_exposure_rule(self) -> None:
        rule = GrossExposureRule()
        ctx = context(request=order_request(qty=1000), price=10.0,
                      gross_value=72_000.0)
        decision = rule.check(ctx, self.limits)
        self.assertFalse(decision.allowed)
        self.assertIn("总仓位", decision.message)

    def test_daily_loss_rule(self) -> None:
        rule = DailyLossRule()
        self.assertFalse(rule.check(context(day_pnl_pct=-0.04), self.limits).allowed)
        self.assertTrue(rule.check(
            context(day_pnl_pct=-0.04,
                    request=order_request(side=SIDE_SELL)), self.limits).allowed)
        self.assertFalse(rule.check(context(day_pnl_pct=-0.06), self.limits).allowed)
        self.assertTrue(rule.check(context(day_pnl_pct=-0.02), self.limits).allowed)

    def test_drawdown_rule(self) -> None:
        rule = DrawdownRule()
        self.assertTrue(rule.check(context(drawdown=0.10), self.limits).allowed)
        self.assertFalse(rule.check(context(drawdown=0.16), self.limits).allowed)
        halt = rule.check(context(drawdown=0.21,
                                  request=order_request(side=SIDE_SELL)), self.limits)
        self.assertFalse(halt.allowed)
        self.assertIn("人工确认", halt.message)

    def test_order_rate_rule(self) -> None:
        rule = OrderRateRule()
        self.assertTrue(rule.check(context(orders=19), self.limits).allowed)
        self.assertFalse(rule.check(context(orders=20), self.limits).allowed)

    def test_price_band_rule(self) -> None:
        rule = PriceBandRule()
        ctx = context(request=order_request(limit_price=11.0), price=10.0)
        self.assertFalse(rule.check(ctx, self.limits).allowed)
        self.assertTrue(rule.check(
            context(request=order_request(limit_price=10.2), price=10.0),
            self.limits).allowed)
        self.assertTrue(rule.check(
            context(request=order_request(limit_price=0.0), price=10.0),
            self.limits).allowed)

    def test_session_rule(self) -> None:
        rule = SessionRule()
        ctx = context(is_open_session=False)
        self.assertTrue(rule.check(ctx, self.limits).allowed)
        limits = RiskLimits(enforce_session=True)
        self.assertFalse(rule.check(ctx, limits).allowed)
        self.assertTrue(rule.check(context(is_open_session=True), limits).allowed)


class ManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.manager = RiskManager(limits=RiskLimits(), bus=self.bus)

    def test_passes_normal_order(self) -> None:
        decision = self.manager.pre_trade(order_request(qty=100), 10.0,
                                          snapshot(), now=DT)
        self.assertTrue(decision.allowed)
        self.assertEqual(self.manager.state()["checks"], 1)

    def test_rejects_and_records(self) -> None:
        events = []
        self.bus.subscribe(RISK_TRIGGERED, lambda event: events.append(event.payload))
        decision = self.manager.pre_trade(order_request(qty=10_000), 10.0,
                                          snapshot(), now=DT)
        self.assertFalse(decision.allowed)
        self.assertEqual(events[0]["rule_id"], "single_trade")
        state = self.manager.state()
        self.assertEqual(state["rejections"]["single_trade"], 1)

    def test_kill_switch_blocks_everything(self) -> None:
        self.manager.trip_kill_switch("测试")
        decision = self.manager.pre_trade(order_request(qty=100), 10.0,
                                          snapshot(), now=DT)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule_id, "kill_switch")
        self.manager.reset_kill_switch()
        self.assertTrue(self.manager.pre_trade(order_request(qty=100), 10.0,
                                               snapshot(), now=DT).allowed)

    def test_order_rate_window(self) -> None:
        for _ in range(20):
            self.manager.pre_trade(order_request(qty=100), 10.0, snapshot(), now=DT)
        decision = self.manager.pre_trade(order_request(qty=100), 10.0,
                                          snapshot(), now=DT)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule_id, "order_rate")

    def test_orders_pruned_after_a_minute(self) -> None:
        for _ in range(20):
            self.manager.pre_trade(order_request(qty=100), 10.0, snapshot(), now=DT)
        later = DT.replace(minute=2)
        self.assertTrue(self.manager.pre_trade(order_request(qty=100), 10.0,
                                               snapshot(), now=later).allowed)

    def test_snapshot_trips_drawdown(self) -> None:
        events = []
        self.bus.subscribe(RISK_STATE_CHANGED, lambda event: events.append(event.payload))
        decision = self.manager.on_snapshot(snapshot(equity=79_000, drawdown=0.21))
        self.assertFalse(decision.allowed)
        self.assertTrue(self.manager.kill_switch)
        self.assertEqual(events[0]["kill_switch"], True)
        self.assertIn("停机线", events[0]["reason"])

    def test_set_limits_and_enabled(self) -> None:
        self.manager.set_limits(single_trade_pct=0.5, unknown=1)
        self.assertEqual(self.manager.limits.single_trade_pct, 0.5)
        self.assertEqual(self.manager.limits.max_orders_per_minute, 20)
        self.manager.set_enabled(["session"])
        self.assertEqual([rule.rule_id for rule in self.manager.rules], ["session"])

    def test_state_shape_and_reset(self) -> None:
        state = self.manager.state()
        for key in ("kill_switch", "rejections", "limits", "enabled",
                    "rule_names", "checks", "drawdown"):
            self.assertIn(key, state)
        self.assertEqual(len(state["enabled"]), 8)
        self.manager.pre_trade(order_request(qty=10_000), 10.0, snapshot(), now=DT)
        self.manager.reset()
        self.assertEqual(self.manager.state()["checks"], 0)
        self.assertEqual(self.manager.state()["rejections"], {})

    def test_position_in_snapshot_used_by_context(self) -> None:
        position = Position(symbol="600519.SH", qty=100, available_qty=100,
                            avg_cost=10.0, last_price=10.0)
        built = self.manager.build_context(
            order_request(qty=100), 10.0, snapshot(positions=(position,)))
        self.assertEqual(built.position_qty, 100)
        self.assertEqual(built.position_value, 1_000.0)


if __name__ == "__main__":
    unittest.main()
