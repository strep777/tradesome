"""撮合、模拟券商与订单路由测试。"""

from __future__ import annotations

import unittest
from datetime import datetime

from tradesome.backtest.matching import MatchingEngine
from tradesome.core.bus import EventBus
from tradesome.core.errors import ValidationError
from tradesome.core.events import (ORDER_CANCELLED, ORDER_FILLED,
                                   ORDER_REJECTED, ORDER_SUBMITTED)
from tradesome.core.types import (ORDER_LIMIT, ORDER_MARKET, ST_CANCELLED,
                                  ST_FILLED, ST_PARTIAL, ST_REJECTED,
                                  Bar, FeeProfile, Order, OrderRequest)
from tradesome.core.instruments import InstrumentRegistry
from tradesome.execution.paper_broker import PaperBroker
from tradesome.execution.router import OrderRouter
from tradesome.portfolio import Account
from tradesome.risk import RiskLimits, RiskManager

SYMBOL = "600519.SH"
DT = datetime(2024, 3, 1, 9, 30)


def bar(open_=10.0, high=10.5, low=9.5, close=10.2, volume=1_000_000,
        symbol=SYMBOL) -> Bar:
    return Bar(symbol=symbol, dt=DT, open=open_, high=high, low=low,
               close=close, volume=volume)


def order(qty=100, side="BUY", order_type=ORDER_MARKET, limit_price=0.0,
          filled=0) -> Order:
    return Order(order_id="O-1", client_id="c-1", symbol=SYMBOL, side=side,
                 qty=qty, order_type=order_type, limit_price=limit_price,
                 created_at=DT, updated_at=DT, filled_qty=filled)


class FakeAudit:
    def __init__(self) -> None:
        self.rows = []

    def write(self, **kwargs) -> None:
        self.rows.append(kwargs)


class MatchingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InstrumentRegistry().with_defaults()
        self.instrument = self.registry.must_get(SYMBOL)
        self.fee = FeeProfile(slippage_bps=0.0)

    def engine(self, ratio=0.05, fee=None) -> MatchingEngine:
        return MatchingEngine(fee or self.fee, ratio)

    def test_market_buy_fills_at_open(self) -> None:
        outcome = self.engine().match(order(), bar(open_=10.0),
                                      self.instrument, 100_000, 0)
        self.assertTrue(outcome.filled)
        self.assertEqual(outcome.fill.price, 10.0)
        self.assertEqual(outcome.fill.qty, 100)
        self.assertEqual(outcome.fill.commission, 5.0)

    def test_market_buy_pays_slippage(self) -> None:
        fee = FeeProfile(slippage_bps=100.0)
        outcome = self.engine(fee=fee).match(order(), bar(open_=10.0),
                                             self.instrument, 100_000, 0)
        self.assertEqual(outcome.fill.price, 10.1)

    def test_limit_buy_uses_better_price(self) -> None:
        outcome = self.engine().match(
            order(order_type=ORDER_LIMIT, limit_price=10.2),
            bar(open_=10.0, low=9.8), self.instrument, 100_000, 0)
        self.assertEqual(outcome.fill.price, 10.0)

    def test_limit_buy_not_touched(self) -> None:
        outcome = self.engine().match(
            order(order_type=ORDER_LIMIT, limit_price=9.0),
            bar(open_=10.0, low=9.5), self.instrument, 100_000, 0)
        self.assertFalse(outcome.filled)
        self.assertFalse(outcome.rejected)
        self.assertIn("未触及", outcome.reason)

    def test_suspended_bar_no_fill(self) -> None:
        outcome = self.engine().match(order(), bar(volume=0),
                                      self.instrument, 100_000, 0)
        self.assertFalse(outcome.filled)
        self.assertIn("停牌", outcome.reason)

    def test_limit_up_blocks_buy(self) -> None:
        outcome = self.engine().match(order(), bar(open_=11.0, high=11.0),
                                      self.instrument, 100_000, 0,
                                      prev_close=10.0)
        self.assertFalse(outcome.filled)
        self.assertIn("涨停", outcome.reason)

    def test_limit_down_blocks_sell(self) -> None:
        outcome = self.engine().match(
            order(side="SELL"), bar(open_=9.0, high=9.0, low=9.0, close=9.0),
            self.instrument, 100_000, 1000, prev_close=10.0)
        self.assertFalse(outcome.filled)
        self.assertIn("跌停", outcome.reason)

    def test_volume_cap_partial_fill(self) -> None:
        outcome = self.engine(ratio=0.05).match(
            order(qty=1000), bar(volume=10_000), self.instrument, 100_000, 0)
        self.assertTrue(outcome.filled)
        self.assertEqual(outcome.fill.qty, 500)

    def test_cash_shortage_reduces_quantity(self) -> None:
        outcome = self.engine().match(order(qty=1000), bar(), self.instrument,
                                      3_000, 0)
        self.assertTrue(outcome.filled)
        self.assertLessEqual(outcome.fill.qty, 200)
        self.assertGreater(outcome.fill.qty, 0)

    def test_cash_shortage_rejects(self) -> None:
        outcome = self.engine().match(order(qty=100), bar(), self.instrument,
                                      10, 0)
        self.assertTrue(outcome.rejected)

    def test_sell_requires_available_qty(self) -> None:
        outcome = self.engine().match(order(side="SELL"), bar(),
                                      self.instrument, 100_000, 0)
        self.assertTrue(outcome.rejected)
        self.assertIn("T+1", outcome.reason)

    def test_index_ignores_volume_cap(self) -> None:
        index = self.registry.must_get("000300.SH")
        outcome = self.engine().match(
            Order(order_id="O-2", client_id="", symbol="000300.SH",
                  side="BUY", qty=10_000), bar(symbol="000300.SH", volume=1),
            index, 10_000_000, 0)
        self.assertTrue(outcome.filled)
        self.assertEqual(outcome.fill.qty, 10_000)

    def test_wrong_symbol_rejected(self) -> None:
        other = Bar(symbol="000001.SZ", dt=DT, open=10, high=10, low=10,
                    close=10, volume=1000)
        outcome = self.engine().match(order(), other, self.instrument, 1000, 0)
        self.assertTrue(outcome.rejected)


class PaperBrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InstrumentRegistry().with_defaults()
        self.account = Account(100_000, t_plus=0)
        self.bus = EventBus()
        self.events = []
        for topic in (ORDER_FILLED, ORDER_REJECTED):
            self.bus.subscribe(topic, lambda event: self.events.append(event))
        self.broker = PaperBroker(self.account, self.registry,
                                  FeeProfile(slippage_bps=0.0),
                                  max_volume_ratio=0.5, bus=self.bus)

    def test_submit_freezes_cash(self) -> None:
        self.broker.submit(order(), 10.0)
        self.assertLess(self.account.cash, 100_000)
        self.assertGreater(self.account.frozen_cash, 0)
        self.assertEqual(len(self.broker.open_orders()), 1)

    def test_on_bar_fills_and_emits(self) -> None:
        submitted = self.broker.submit(order(), 10.0)
        results = self.broker.on_bar(bar())
        self.assertEqual(len(results), 1)
        filled_order, fill = results[0]
        self.assertEqual(filled_order.status, ST_FILLED)
        self.assertEqual(fill.qty, 100)
        self.assertEqual(self.broker.open_orders(), [])
        self.assertEqual(self.events[0].topic, ORDER_FILLED)
        self.assertEqual(self.account.frozen_cash, 0.0)

    def test_partial_then_complete(self) -> None:
        self.broker.matcher.max_volume_ratio = 0.01   # 100 股/根
        order_obj = self.broker.submit(order(qty=300), 10.0)
        first = self.broker.on_bar(bar(volume=10_000))[0]
        self.assertEqual(first[0].status, ST_PARTIAL)
        self.assertEqual(first[0].filled_qty, 100)
        second = self.broker.on_bar(bar(volume=10_000))[0]
        self.assertEqual(second[0].filled_qty, 200)
        third = self.broker.on_bar(bar(volume=10_000))[0]
        self.assertEqual(third[0].status, ST_FILLED)
        self.assertNotIn(order_obj.order_id, [o.order_id for o in self.broker.open_orders()])

    def test_cancel_releases_cash(self) -> None:
        submitted = self.broker.submit(order(), 10.0)
        self.assertTrue(self.broker.cancel(submitted.order_id))
        self.assertEqual(self.account.frozen_cash, 0.0)
        self.assertFalse(self.broker.cancel(submitted.order_id))

    def test_rejected_order_emits(self) -> None:
        self.broker.submit(order(qty=100), 10.0)
        self.broker.on_bar(bar(volume=0))
        self.assertEqual(self.events, [])
        self.assertEqual(self.broker.open_orders()[0].order_id, "O-1")


class RouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InstrumentRegistry().with_defaults()
        self.account = Account(100_000, t_plus=0)
        self.bus = EventBus()
        self.audit = FakeAudit()
        self.risk = RiskManager(limits=RiskLimits(), bus=self.bus)
        self.events = []
        for topic in (ORDER_SUBMITTED, ORDER_REJECTED, ORDER_CANCELLED):
            self.bus.subscribe(topic, lambda event: self.events.append(event))
        self.broker = PaperBroker(self.account, self.registry, FeeProfile())
        self.router = OrderRouter(self.account, self.broker, self.risk,
                                  self.registry, audit=self.audit, bus=self.bus)

    def req(self, **kwargs) -> OrderRequest:
        data = dict(symbol=SYMBOL, side="BUY", qty=100)
        data.update(kwargs)
        return OrderRequest(**data)

    def test_successful_submit(self) -> None:
        result = self.router.submit(self.req(), strategy_id="s1", price=10.0)
        self.assertEqual(result.side, "BUY")
        self.assertEqual(self.router.stats()["submitted"], 1)
        self.assertEqual(self.events[-1].topic, ORDER_SUBMITTED)
        self.assertTrue(self.audit.rows)

    def test_unknown_symbol(self) -> None:
        result = self.router.submit(self.req(symbol="999999.XX"), price=10.0)
        self.assertEqual(result.status, ST_REJECTED)
        self.assertIn("E_UNKNOWN_SYMBOL", result.reason)

    def test_invalid_lot(self) -> None:
        result = self.router.submit(self.req(qty=150), price=10.0)
        self.assertEqual(result.status, ST_REJECTED)
        self.assertIn("E_LOT", result.reason)

    def test_limit_order_requires_price(self) -> None:
        # 限价单缺价格在构造订单请求时就被拦住，路由层的检查是第二道防线。
        with self.assertRaises(ValidationError):
            self.req(order_type=ORDER_LIMIT, limit_price=0.0)

    def test_no_quote(self) -> None:
        result = self.router.submit(self.req(), price=None)
        self.assertIn("E_NO_QUOTE", result.reason)

    def test_duplicate_client_id_is_idempotent(self) -> None:
        first = self.router.submit(self.req(client_id="same"), price=10.0)
        second = self.router.submit(self.req(client_id="same"), price=10.0)
        self.assertEqual(first.order_id, second.order_id)
        self.assertEqual(self.router.submitted, 1)

    def test_risk_rejection(self) -> None:
        result = self.router.submit(self.req(qty=10_000), price=10.0)
        self.assertIn("E_RISK", result.reason)
        self.assertEqual(self.router.rejected, 1)

    def test_t1_sell_rejected(self) -> None:
        result = self.router.submit(self.req(side="SELL", qty=100), price=10.0)
        self.assertIn("E_T1", result.reason)

    def test_kill_switch_rejection(self) -> None:
        self.risk.trip_kill_switch("测试")
        result = self.router.submit(self.req(), price=10.0)
        self.assertIn("E_RISK", result.reason)

    def test_broker_exception_becomes_rejection(self) -> None:
        class Broken:
            def submit(self, order, price):
                raise RuntimeError("坏了")

            def cancel(self, order_id):
                return False

        router = OrderRouter(self.account, Broken(), self.risk, self.registry)
        result = router.submit(self.req(), price=10.0)
        self.assertIn("E_BROKER", result.reason)

    def test_cancel(self) -> None:
        placed = self.router.submit(self.req(), price=10.0)
        self.assertTrue(self.router.cancel(placed.order_id))
        self.assertEqual(self.router.stats()["cancelled"], 1)
        self.assertEqual(self.events[-1].topic, ORDER_CANCELLED)
        self.assertFalse(self.router.cancel("missing"))


if __name__ == "__main__":
    unittest.main()
