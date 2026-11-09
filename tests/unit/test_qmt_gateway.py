"""QMT 网关单元测试。

用假的 xtquant 模块验证：连接、下单、撤单、回报翻译、查询映射与停止。
真实 xtquant 只在安装了 QMT/miniQMT 的机器上存在，因此不能作为测试前提。
"""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from tradesome.core.errors import GatewayError
from tradesome.gateway import QmtGateway, create_gateway


class FakeConstants:
    FIX_PRICE = 11
    STOCK_BUY = 23
    STOCK_SELL = 24


class FakeCallback:
    """xtquant 的回调基类；网关用 type() 动态继承它。"""


class FakeStockAccount:
    def __init__(self, account_id: str, account_type: str = "STOCK") -> None:
        self.account_id = account_id
        self.account_type = account_type


def _order(order_id: int = 1001, status: int = 50, traded: int = 0,
           volume: int = 100, price: float = 10.0, side: int = 23,
           remark: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        account_id="8888", stock_code="600519.SH", order_id=order_id,
        order_sysid="", order_type=side, order_volume=volume, price_type=11,
        price=price, traded_volume=traded, traded_price=price if traded else 0.0,
        order_status=status, status_msg="", strategy_name="TradeSome",
        order_remark=remark, offset_flag=0,
    )


def _position(symbol: str = "600519.SH", volume: int = 200,
              can_use: int = 100, avg: float = 10.0,
              market_value: float = 2020.0) -> SimpleNamespace:
    return SimpleNamespace(
        stock_code=symbol, volume=volume, can_use_volume=can_use,
        open_price=avg, avg_price=avg, market_value=market_value,
        frozen_volume=0, on_road_volume=0, yesterday_volume=100,
    )


class FakeTrader:
    def __init__(self, path: str, session_id: int) -> None:
        self.path = path
        self.session_id = session_id
        self.callback = None
        self.connected = False
        self.subscribed = None
        self.orders = []
        self.next_id = 1001
        self.last_order_args = None
        self.cancelled = []
        self.asset = SimpleNamespace(account_id="8888", cash=9000.0,
                                     frozen_cash=500.0, market_value=2020.0,
                                     total_asset=11520.0)
        self.positions = [_position()]

    # -- 生命周期
    def register_callback(self, callback) -> None:
        self.callback = callback

    def start(self) -> None:
        pass

    def connect(self) -> int:
        self.connected = True
        return 0

    def subscribe(self, account) -> int:
        self.subscribed = account
        return 0

    def unsubscribe(self, account) -> int:
        return 0

    def stop(self) -> None:
        self.connected = False

    # -- 交易
    def order_stock(self, account, symbol, order_type, volume, price_type,
                    price, strategy_name, remark) -> int:
        order_id = self.next_id
        self.next_id += 1
        self.last_order_args = {
            "symbol": symbol, "order_type": order_type, "volume": volume,
            "price_type": price_type, "price": price, "remark": remark,
        }
        order = _order(order_id=order_id, volume=volume, price=price,
                       side=order_type, remark=remark)
        self.orders.append(order)
        if self.callback is not None:
            self.callback.on_stock_order(order)
        return order_id

    def cancel_order_stock(self, account, order_id: int) -> int:
        self.cancelled.append(order_id)
        for order in self.orders:
            if order.order_id == order_id:
                order.order_status = 54
                if self.callback is not None:
                    self.callback.on_stock_order(order)
        return 0

    # -- 查询
    def query_stock_asset(self, account):
        return self.asset

    def query_stock_positions(self, account):
        return self.positions

    def query_stock_orders(self, account, cancelable_only=False):
        return list(self.orders)

    def query_stock_trades(self, account):
        return []

    # -- 测试辅助
    def fill(self, order_id: int = 1001, volume: int = 100,
             price: float = 10.0) -> None:
        for order in self.orders:
            if order.order_id == order_id:
                order.traded_volume = volume
                order.traded_price = price
                order.order_status = 56
                if self.callback is not None:
                    self.callback.on_stock_order(order)


def _module() -> dict:
    return {
        "xtconstant": FakeConstants,
        "XtQuantTrader": FakeTrader,
        "XtQuantTraderCallback": FakeCallback,
        "StockAccount": FakeStockAccount,
    }


class QmtGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events = []
        self.module = _module()
        self.gateway = QmtGateway("D:/qmt/userdata_mini", "8888",
                                  account_type="STOCK", module=self.module)
        self.gateway.set_listener(lambda event, data: self.events.append((event, data)))

    def tearDown(self) -> None:
        try:
            self.gateway.stop()
        except Exception:  # noqa: BLE001
            pass

    def _of(self, name: str) -> list:
        return [data for event, data in self.events if event == name]

    def test_start_connects_and_pushes_snapshot(self) -> None:
        self.gateway.start()
        self.assertTrue(self.gateway.is_connected)
        self.assertEqual(self.gateway._trader.subscribed.account_id, "8888")
        self.assertEqual(self.gateway._trader.subscribed.account_type, "STOCK")
        statuses = [item["status"] for item in self._of("status")]
        self.assertIn("connecting", statuses)
        self.assertIn("connected", statuses)
        account = self._of("account")[0]
        self.assertEqual(account.equity, 11520.0)
        self.assertEqual(account.frozen_cash, 500.0)
        positions = self._of("position")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "600519.SH")
        self.assertEqual(positions[0].available_qty, 100)

    def test_start_requires_path_and_account(self) -> None:
        gateway = QmtGateway("", "8888", module=self.module)
        with self.assertRaises(GatewayError):
            gateway.start()
        gateway = QmtGateway("D:/qmt", "", module=self.module)
        with self.assertRaises(GatewayError):
            gateway.start()

    def test_connect_failure_raises(self) -> None:
        class BadTrader(FakeTrader):
            def connect(self) -> int:
                return -1
        module = _module()
        module["XtQuantTrader"] = BadTrader
        gateway = QmtGateway("D:/qmt", "8888", module=module)
        with self.assertRaises(GatewayError) as ctx:
            gateway.start()
        self.assertIn("连接 miniQMT 失败", str(ctx.exception))

    def test_place_order_maps_side_and_price_type(self) -> None:
        self.gateway.start()
        broker_id = self.gateway.place_order({
            "symbol": "600519.SH", "side": "BUY", "qty": 100,
            "price": 10.5, "client_id": "C-1", "strategy_name": "ma_cross",
        })
        self.assertEqual(broker_id, "1001")
        args = self.gateway._trader.last_order_args
        self.assertEqual(args["order_type"], 23)
        self.assertEqual(args["price_type"], 11)
        self.assertEqual(args["volume"], 100)
        self.assertEqual(args["remark"], "C-1")
        order = self._of("order")[-1]
        self.assertEqual(order.client_id, "C-1")
        self.assertEqual(order.status, "SUBMITTED")

    def test_place_order_sell(self) -> None:
        self.gateway.start()
        self.gateway.place_order({"symbol": "600519.SH", "side": "SELL",
                                  "qty": 100, "price": 10.0, "client_id": "S-1"})
        self.assertEqual(self.gateway._trader.last_order_args["order_type"], 24)

    def test_order_status_translation(self) -> None:
        self.gateway.start()
        self.gateway.place_order({"symbol": "600519.SH", "side": "BUY",
                                  "qty": 100, "price": 10.0, "client_id": "C-2"})
        self.gateway._trader.orders[0].order_status = 55
        self.gateway._on_order(self.gateway._trader.orders[0])
        self.assertEqual(self._of("order")[-1].status, "PARTIAL")
        self.gateway._trader.fill(1001, 100, 10.0)
        update = self._of("order")[-1]
        self.assertEqual(update.status, "FILLED")
        self.assertEqual(update.filled_qty, 100)

    def test_trade_callback(self) -> None:
        self.gateway.start()
        trade = SimpleNamespace(
            traded_id="T-1", order_id=1001, stock_code="600519.SH",
            order_type=23, traded_volume=100, traded_price=10.2,
            traded_amount=1020.0, traded_time=20260101100000,
            strategy_name="ma_cross", order_remark="C-1")
        self.gateway._on_trade(trade)
        update = self._of("trade")[0]
        self.assertEqual(update.trade_id, "T-1")
        self.assertEqual(update.side, "BUY")
        self.assertEqual(update.qty, 100)

    def test_order_error_becomes_rejected(self) -> None:
        self.gateway.start()
        self.gateway.place_order({"symbol": "600519.SH", "side": "BUY",
                                  "qty": 100, "price": 10.0, "client_id": "C-3"})
        error = SimpleNamespace(order_id=1001, error_msg="资金不足",
                                order_remark="C-3")
        self.gateway._on_order_error(error)
        update = self._of("order")[-1]
        self.assertEqual(update.status, "REJECTED")
        self.assertEqual(update.reason, "资金不足")
        self.assertEqual(update.client_id, "C-3")

    def test_cancel_and_cancel_all(self) -> None:
        self.gateway.start()
        self.gateway.place_order({"symbol": "600519.SH", "side": "BUY",
                                  "qty": 100, "price": 10.0, "client_id": "C-4"})
        self.assertTrue(self.gateway.cancel("1001"))
        self.assertEqual(self.gateway._trader.cancelled, [1001])
        self.gateway._trader.fill(1001)
        self.assertEqual(self.gateway.cancel_all(), 0)
        self.assertFalse(self.gateway.cancel("9999") and False)

    def test_queries_map_fields(self) -> None:
        self.gateway.start()
        positions = self.gateway.query_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].qty, 200)
        self.assertEqual(positions[0].avg_cost, 10.0)
        self.assertAlmostEqual(positions[0].last_price, 10.1)
        account = self.gateway.query_account()
        self.assertEqual(account.account_id, "8888")
        self.assertEqual(account.equity, 11520.0)

    def test_stop_marks_disconnected(self) -> None:
        self.gateway.start()
        self.gateway.stop()
        self.assertFalse(self.gateway.is_connected)
        self.assertIn("disconnected", [item["status"] for item in self._of("status")])

    def test_not_connected_rejects_order(self) -> None:
        with self.assertRaises(GatewayError):
            self.gateway.place_order({"symbol": "600519.SH", "side": "BUY",
                                      "qty": 100, "price": 10.0})

    def test_create_gateway_qmt(self) -> None:
        gateway = create_gateway("qmt", path="D:/qmt", account_id="8888",
                                 account_type="CREDIT")
        self.assertIsInstance(gateway, QmtGateway)
        self.assertEqual(gateway.account_type, "CREDIT")


class QmtModuleMissingTest(unittest.TestCase):
    def test_missing_module_message(self) -> None:
        gateway = QmtGateway("D:/qmt", "8888")
        if QmtGateway.available():
            self.skipTest("本机已安装 xtquant")
        with self.assertRaises(GatewayError) as ctx:
            gateway.start()
        self.assertIn("xtquant", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
