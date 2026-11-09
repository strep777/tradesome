"""实盘服务（LiveTradeService）测试：解锁门禁、模拟会话、下单与收尾。

不联网：只用内置模拟柜台。桥接客户端的协议行为在
tests/unit/test_qmt_gateway.py 里用假 xtquant 模块验证。
"""

from __future__ import annotations

import unittest

from tests.support import TempWorkspace, build_memory_container, make_bars
from tradesome.core.errors import TradingError
from tradesome.core.types import ST_CANCELLED, ST_FILLED


class LiveTradeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TempWorkspace()
        self.container = build_memory_container(root=self.workspace.root)
        self.service = self.container.services.live_trade
        self.service.config.live.enabled = True
        self.service.config.live.max_order_value = 100000.0

    def tearDown(self) -> None:
        try:
            self.service.stop_session()
        finally:
            self.container.close()
            self.workspace.cleanup()

    def _start(self, **kwargs):
        self.service.arm()
        options = dict(gateway_kind="sim", symbols=["600519.SH"],
                       initial_cash=100000.0, auto_trade=False)
        options.update(kwargs)
        return self.service.start_session(**options)

    def test_requires_enable_and_arm(self) -> None:
        self.service.config.live.enabled = False
        with self.assertRaises(TradingError):
            self.service.arm()
        self.service.config.live.enabled = True
        with self.assertRaises(TradingError):
            self.service.start_session(gateway_kind="sim")
        self.service.arm()
        self.assertTrue(self.service.armed)

    def test_sim_session_order_and_positions(self) -> None:
        status = self._start()
        self.assertTrue(self.service.connected)
        self.assertEqual(self.service.status, "connected")
        self.assertEqual(status["gateway_kind"], "sim")

        self.assertTrue(self.service.feed_tick("600519.SH", 100.0))
        order = self.service.place_order("600519.SH", "BUY", 100, price=101.0)
        self.assertEqual(order.status, ST_FILLED)
        self.assertEqual(order.filled_qty, 100)
        self.assertEqual(order.gateway, "sim")

        positions = self.service.positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["qty"], 100)
        self.assertEqual(positions[0]["available"], 0)  # T+1
        snapshot = self.service.account_snapshot()
        self.assertLess(snapshot["equity"], 100000.0)
        self.assertTrue(self.service.orders())
        self.assertTrue(self.service.trades())

    def test_rejects_over_limit_and_unarmed(self) -> None:
        self._start()
        with self.assertRaises(TradingError):
            self.service.place_order("600519.SH", "BUY", 100, price=2000.0)
        self.service.disarm()
        with self.assertRaises(TradingError):
            self.service.place_order("600519.SH", "BUY", 100, price=10.0)

    def test_cancel_open_order(self) -> None:
        self._start()
        self.assertTrue(self.service.feed_tick("600519.SH", 100.0))
        order = self.service.place_order("600519.SH", "BUY", 100, price=50.0)
        self.assertTrue(order.is_open)
        self.assertTrue(self.service.cancel(order.order_id))
        updated = self.service._oms.get(order.order_id)
        self.assertEqual(updated.status, ST_CANCELLED)

    def test_auto_trade_without_data_is_reported(self) -> None:
        self._start(strategy_key="ma_cross")
        result = self.service.run_strategy_once(force=True)
        self.assertFalse(result["ok"])
        self.assertIn("没有历史行情", result["message"])

    def test_auto_trade_with_bars(self) -> None:
        bars = make_bars(days=300)
        self.container.repos.bars.upsert_bars(bars)
        self._start(strategy_key="ma_cross")
        result = self.service.run_strategy_once(force=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["strategy"], "ma_cross")

    def test_stop_session(self) -> None:
        self._start()
        result = self.service.stop_session("测试结束")
        self.assertEqual(result["status"], "idle")
        self.assertFalse(self.service.connected)
        self.assertFalse(self.service.armed)


if __name__ == "__main__":
    unittest.main()
