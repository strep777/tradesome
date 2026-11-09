"""网关协议、模拟柜台、委托同步与实盘 Broker 测试。

这些测试只依赖标准库和本项目模块，不需要网络，也不需要安装 QMT。
QMT 网关的真实 xtquant 调用在 test_qmt_gateway.py 里用假模块验证。
"""

from __future__ import annotations

import unittest
from datetime import datetime

from tradesome.core.errors import GatewayAuthError, GatewayError, TradingError
from tradesome.core.types import ST_CANCELLED, ST_FILLED, ST_REJECTED, ST_SUBMITTED, Order
from tradesome.gateway import QmtGateway, create_gateway
from tradesome.gateway.base import BaseGateway
from tradesome.gateway.protocol import (
    AccountUpdate, OrderUpdate, PositionUpdate, TickUpdate, TradeUpdate,
    account_from_dict, dumps, make_error, make_event, make_request,
    make_response, order_from_dict, parse_message, position_from_dict,
    trade_from_dict,
)
from tradesome.gateway.sim import SimGateway
from tradesome.trading.live_broker import LiveBroker
from tradesome.trading.oms import OrderManager, map_status


def sample_order(order_id: str = "O-1", side: str = "BUY", qty: int = 100,
                 order_type: str = "LIMIT", limit_price: float = 10.0) -> Order:
    return Order(order_id=order_id, client_id="C-" + order_id, symbol="600519.SH",
                 side=side, qty=qty, order_type=order_type, limit_price=limit_price)


class ProtocolTest(unittest.TestCase):
    def test_request_response_event_roundtrip(self) -> None:
        request = make_request("7", "order", {"symbol": "600519.SH"})
        self.assertEqual(request["v"], 1)
        self.assertEqual(request["id"], "7")
        kind, payload = parse_message(dumps(request))
        self.assertEqual(kind, "request")
        self.assertEqual(payload["cmd"], "order")

        response = make_response("7", {"order_id": "B-1"})
        kind, payload = parse_message(dumps(response))
        self.assertEqual(kind, "response")
        self.assertTrue(payload["ok"])

        error = make_error("7", "E_REJECTED", "资金不足")
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "E_REJECTED")

        event = make_event("order", {"status": "FILLED"})
        kind, payload = parse_message(dumps(event))
        self.assertEqual(kind, "event")
        self.assertEqual(payload["event"], "order")

    def test_parse_message_accepts_dict(self) -> None:
        kind, payload = parse_message(make_event("status", {"status": "connected"}))
        self.assertEqual(kind, "event")

    def test_dataclass_parsing_is_tolerant(self) -> None:
        account = account_from_dict({"balance": 1234.5, "available": 1200,
                                     "frozen": 34.5, "market_value": 500,
                                     "equity": 1734.5, "account_id": "A1"})
        self.assertEqual(account.cash, 1234.5)
        self.assertEqual(account.available, 1200.0)
        self.assertEqual(account.account_id, "A1")

        position = position_from_dict({"symbol": "000001.SZ", "volume": 300,
                                       "available": 200, "avg_price": 10.5,
                                       "last_price": 11.0})
        self.assertEqual(position.symbol, "000001.SZ")
        self.assertEqual(position.qty, 300)

        order = order_from_dict({"order_id": "B-9", "client_id": "C-9",
                                 "symbol": "600519.SH", "side": "BUY", "qty": 100,
                                 "traded": 50, "price": 10.0, "status": "PARTTRADED",
                                 "reason": ""})
        self.assertEqual(order.filled_qty, 50)
        self.assertEqual(order.status, "PARTTRADED")

        trade = trade_from_dict({"trade_id": "T1", "symbol": "600519.SH",
                                 "side": "BUY", "volume": 100, "price": 10.2,
                                 "fees": 5.0})
        self.assertEqual(trade.trade_id, "T1")
        self.assertEqual(trade.qty, 100)


class FakeGateway(BaseGateway):
    """只记录调用的最小网关，用来测 LiveBroker 的边界。"""

    name = "fake"

    def __init__(self, connected: bool = True) -> None:
        super().__init__()
        self.connected = connected
        self.placed: list = []
        self.cancelled: list = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    def start(self) -> None:
        self.connected = True

    def stop(self) -> None:
        self.connected = False

    def place_order(self, order):
        self.placed.append(dict(order))
        return "B-%d" % len(self.placed)

    def cancel(self, broker_order_id, symbol=""):
        self.cancelled.append(broker_order_id)
        return True

    def cancel_all(self, symbol=""):
        return 0

    def query_account(self):
        return AccountUpdate()

    def query_positions(self):
        return []

    def query_orders(self):
        return []


class SimGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list = []
        self.gateway = SimGateway(initial_cash=100000.0, auto_fill=False)
        self.gateway.set_listener(lambda event, data: self.events.append((event, data)))
        self.gateway.start()
        self.gateway.feed_tick(TickUpdate(symbol="600519.SH", last_price=100.0))

    def tearDown(self) -> None:
        self.gateway.stop()

    def test_connect_emits_account(self) -> None:
        kinds = [e[0] for e in self.events]
        self.assertIn("status", kinds)
        self.assertIn("account", kinds)
        self.assertEqual(self.gateway.query_account().cash, 100000.0)

    def test_limit_buy_freezes_cash_and_then_fills(self) -> None:
        broker_id = self.gateway.place_order({
            "symbol": "600519.SH", "side": "BUY", "qty": 100,
            "price": 101.0, "order_type": "LIMIT", "client_id": "C-1",
        })
        self.assertTrue(broker_id.startswith("SIM-"))
        self.assertAlmostEqual(self.gateway.cash, 100000 - 101 * 100 - 5.1, places=2)
        self.assertGreater(self.gateway.frozen_cash, 0)

        self.gateway.force_fill(broker_id, qty=50, price=100.5)
        self.gateway.force_fill(broker_id, qty=50, price=100.5)
        orders = {o.broker_order_id: o for o in self.gateway.query_orders()}
        filled = orders[broker_id]
        self.assertEqual(filled.status, "FILLED")
        self.assertEqual(filled.filled_qty, 100)
        # 撮合按更优现价成交：现价 100.0 低于委托价 101.0
        self.assertAlmostEqual(filled.avg_price, 100.0, places=2)
        self.assertEqual(self.gateway.frozen_cash, 0.0)
        positions = {p.symbol: p for p in self.gateway.query_positions()}
        self.assertEqual(positions["600519.SH"].qty, 100)
        self.assertEqual(positions["600519.SH"].available_qty, 0)  # T+1

    def test_cancel_releases_frozen_cash(self) -> None:
        before = self.gateway.cash
        broker_id = self.gateway.place_order({
            "symbol": "600519.SH", "side": "BUY", "qty": 100,
            "price": 101.0, "order_type": "LIMIT", "client_id": "C-2",
        })
        self.assertLess(self.gateway.cash, before)
        self.assertTrue(self.gateway.cancel(broker_id))
        self.assertAlmostEqual(self.gateway.cash, before, places=2)
        self.assertFalse(self.gateway.cancel(broker_id))  # 已撤，不能重复撤

    def test_t_plus_one_blocks_sell(self) -> None:
        broker_id = self.gateway.place_order({
            "symbol": "600519.SH", "side": "BUY", "qty": 100,
            "price": 101.0, "order_type": "LIMIT", "client_id": "C-3",
        })
        self.gateway.force_fill(broker_id, qty=100, price=100.0)
        with self.assertRaises(TradingError) as ctx:
            self.gateway.place_order({
                "symbol": "600519.SH", "side": "SELL", "qty": 100,
                "price": 101.0, "order_type": "LIMIT", "client_id": "C-4",
            })
        self.assertIn("可卖", str(ctx.exception))

    def test_rejects_bad_orders(self) -> None:
        with self.assertRaises(GatewayError):
            self.gateway.place_order({"symbol": "", "side": "BUY", "qty": 100,
                                      "price": 10.0})
        with self.assertRaises(GatewayError):
            self.gateway.place_order({"symbol": "600519.SH", "side": "BUY",
                                      "qty": 0, "price": 10.0})
        with self.assertRaises(GatewayError):
            self.gateway.place_order({"symbol": "600519.SH", "side": "BUY",
                                      "qty": 100, "price": 0.0})

    def test_market_order_needs_tick(self) -> None:
        gateway = SimGateway(initial_cash=100000.0, auto_fill=False)
        gateway.start()
        with self.assertRaises(TradingError):
            gateway.place_order({"symbol": "600519.SH", "side": "BUY", "qty": 100,
                                 "price": 0.0, "order_type": "MARKET"})
        gateway.stop()


class CreateGatewayTest(unittest.TestCase):
    def test_known_kinds(self) -> None:
        sim = create_gateway("sim", initial_cash=50000.0, unknown="ignored")
        self.assertIsInstance(sim, SimGateway)
        qmt = create_gateway("qmt", path="D:/qmt/userdata_mini", account_id="123",
                             account_type="STOCK", unknown="ignored")
        self.assertIsInstance(qmt, QmtGateway)
        self.assertEqual(qmt.account_id, "123")
        self.assertEqual(qmt.account_type, "STOCK")

    def test_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            create_gateway("ctp")


class LiveBrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGateway()
        self.broker = LiveBroker(self.fake, gateway_name="fake", protect_pct=0.01)

    def test_limit_order_keeps_price(self) -> None:
        order = sample_order(limit_price=10.0)
        placed = self.broker.submit(order, 9.9)
        self.assertEqual(placed.status, ST_SUBMITTED)
        self.assertEqual(placed.limit_price, 10.0)
        self.assertEqual(placed.broker_order_id, "B-1")
        self.assertEqual(placed.gateway, "fake")
        self.assertEqual(self.fake.placed[0]["order_type"], "LIMIT")

    def test_market_order_becomes_protective_limit(self) -> None:
        order = sample_order(order_type="MARKET", limit_price=0.0)
        placed = self.broker.submit(order, 10.0)
        self.assertEqual(placed.limit_price, 10.1)
        self.assertEqual(placed.order_type, "LIMIT")

    def test_not_connected_raises(self) -> None:
        self.fake.connected = False
        with self.assertRaises(TradingError):
            self.broker.submit(sample_order(), 10.0)

    def test_cancel_unknown_id(self) -> None:
        self.assertFalse(self.broker.cancel("O-missing"))


class OrderManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = OrderManager()

    def test_status_mapping(self) -> None:
        self.assertEqual(map_status("AllTraded"), ST_FILLED)
        self.assertEqual(map_status("PARTTRADED"), "PARTIAL")
        self.assertEqual(map_status("Canceled"), ST_CANCELLED)
        self.assertEqual(map_status("WeirdState"), "UNKNOWN")

    def test_register_keeps_filled_status(self) -> None:
        manager = self.manager
        manager.register(sample_order())
        manager.apply_update(OrderUpdate(
            client_id="C-O-1", broker_order_id="B-1", symbol="600519.SH",
            side="BUY", qty=100, filled_qty=100, price=10.0, avg_price=10.0,
            status="ALLTRADED", updated_at=datetime.now()))
        order = manager.register(sample_order())
        self.assertEqual(order.status, ST_FILLED)  # 不能被 submit 返回的旧状态覆盖

    def test_apply_update_creates_external_order(self) -> None:
        order = self.manager.apply_update(OrderUpdate(
            client_id="", broker_order_id="B-X", symbol="600519.SH", side="SELL",
            qty=200, filled_qty=0, price=11.0, avg_price=0.0,
            status="NOTTRADED", updated_at=datetime.now()))
        self.assertIsNotNone(order)
        self.assertTrue(order.order_id.startswith("EXT-"))
        self.assertEqual(self.manager.get(order.order_id).qty, 200)

    def test_apply_trade_dedupes_by_trade_id(self) -> None:
        self.manager.register(sample_order())
        update = TradeUpdate(trade_id="T-1", client_id="C-O-1",
                             broker_order_id="B-1", symbol="600519.SH",
                             side="BUY", qty=100, price=10.1, fees=5.0,
                             dt=datetime.now())
        first = self.manager.apply_trade(update)
        second = self.manager.apply_trade(update)
        self.assertIsNotNone(first)
        self.assertEqual(first.fill_id, "BRK-T-1")
        self.assertIsNone(second)
        self.assertEqual(len(self.manager.fills()), 1)

    def test_mark_unknown_only_open_orders(self) -> None:
        self.manager.register(sample_order("O-1"))
        closed = sample_order("O-2").evolve(status=ST_FILLED)
        self.manager.register(closed)
        changed = self.manager.mark_unknown("断线")
        self.assertEqual([o.order_id for o in changed], ["O-1"])
        self.assertEqual(self.manager.get("O-1").status, "UNKNOWN")
        self.assertEqual(self.manager.get("O-2").status, ST_FILLED)

    def test_reject_status_maps(self) -> None:
        order = self.manager.apply_update(OrderUpdate(
            client_id="C-O-1", broker_order_id="B-1", symbol="600519.SH",
            side="BUY", qty=100, filled_qty=0, price=10.0, avg_price=0.0,
            status="REJECTED", reason="资金不足", updated_at=datetime.now()))
        self.assertIsNotNone(order)
        self.assertEqual(order.status, ST_REJECTED)
        self.assertIn("资金不足", order.reason)


if __name__ == "__main__":
    unittest.main()
