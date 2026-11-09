"""应用服务层集成测试：数据、策略、回测、模拟盘与报告。"""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from tradesome.app.bootstrap import build_container, shutdown
from tradesome.core.errors import DataError, StrategyLoadError
from tradesome.strategy.sandbox import check_source
from tests.logging_utils import close_log_handlers
from tests.support import TempWorkspace

SYMBOL = "600519.SH"


def wait_until(predicate, timeout=30.0, interval=0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class ServiceTestCase(unittest.TestCase):
    with_demo = True

    def setUp(self) -> None:
        self.ws = TempWorkspace()
        self.container = build_container(root=self.ws.root, memory_db=True,
                                         with_demo=self.with_demo)
        self.services = self.container.services
        if self.with_demo and not self.services.market.summary()["bars"]:
            self.services.market.generate_demo()

    def tearDown(self) -> None:
        shutdown(self.container)
        close_log_handlers()
        self.ws.cleanup()


class MarketServiceTest(ServiceTestCase):
    def test_demo_universe(self) -> None:
        quotes = self.services.market.quotes()
        self.assertGreaterEqual(len(quotes), 8)
        active = [q for q in quotes if q["has_data"]]
        self.assertEqual(len(active), 8)
        self.assertTrue(all(q["price"] > 0 for q in active))
        summary = self.services.market.summary()
        self.assertEqual(summary["symbols"], 8)
        self.assertGreater(summary["bars"], 1000)

    def test_candles_and_history(self) -> None:
        bars = self.services.market.candles(SYMBOL, limit=30)
        self.assertEqual(len(bars), 30)
        self.assertEqual(bars, sorted(bars, key=lambda b: b.dt))
        history = self.services.market.history(SYMBOL, start=bars[0].dt.date(),
                                               end=bars[-1].dt.date())
        self.assertGreaterEqual(len(history), 30)
        self.assertEqual(self.services.market.last_price(SYMBOL), bars[-1].close)

    def test_coverage_and_quality(self) -> None:
        rows = self.services.market.coverage()
        self.assertTrue(rows)
        report = self.services.market.quality(SYMBOL)
        self.assertTrue(report.ok)
        self.assertIsInstance(report.issues, list)

    def test_export_import_roundtrip(self) -> None:
        before = self.services.market.summary()["bars"]
        path = self.services.market.export_csv(SYMBOL, target_dir=self.ws.root)
        self.assertTrue(Path(path).exists())
        self.services.market.delete(SYMBOL)
        self.assertIsNone(self.services.market.last_price(SYMBOL))
        result = self.services.market.import_csv(path)
        self.assertEqual(result["symbol"], SYMBOL)
        self.assertGreater(result["bars"], 100)
        self.assertEqual(self.services.market.summary()["bars"], before)


class StrategyServiceTest(ServiceTestCase):
    def test_builtin_catalog(self) -> None:
        metas = self.services.strategy.metas()
        self.assertGreaterEqual(len(metas), 6)
        keys = [m["key"] for m in metas]
        self.assertIn("ma_cross", keys)
        specs = self.services.strategy.params("ma_cross")
        self.assertTrue(any(s["name"] == "fast" for s in specs))
        self.assertEqual(self.services.strategy.defaults("ma_cross")["fast"], 5)
        strategy = self.services.strategy.create("ma_cross", {"fast": 3})
        self.assertEqual(strategy.p["fast"], 3)

    def test_template_is_valid(self) -> None:
        source = self.services.strategy.template()
        self.assertIn("class ", source)
        self.assertEqual(check_source(source), [])

    def test_save_and_delete_user_strategy(self) -> None:
        code = self.services.strategy.template().replace(
            'key="my_strategy"', 'key="my_test"')
        key = self.services.strategy.save_user("my_test", code)
        self.assertEqual(key, "my_test")
        self.assertTrue(self.services.strategy.meta("my_test")["is_user"])
        self.assertIn("class MyStrategy",
                      self.services.strategy.user_source("my_test"))
        self.assertIn("my_test", [m["key"] for m in self.services.strategy.metas()])
        self.services.strategy.delete_user("my_test")
        with self.assertRaises(Exception):
            self.services.strategy.meta("my_test")

    def test_save_broken_code_reports_errors(self) -> None:
        code = "import os\nclass X(Strategy):\n    pass\n"
        with self.assertRaises(StrategyLoadError) as caught:
            self.services.strategy.save_user("broken_test", code)
        self.assertTrue(str(caught.exception))

    def test_save_rejects_bad_filename(self) -> None:
        code = self.services.strategy.template()
        with self.assertRaises(Exception):
            self.services.strategy.save_user("evil", code, filename="../evil.py")


class BacktestServiceTest(ServiceTestCase):
    def make_config(self, symbols=(SYMBOL,)):
        coverage = self.services.market.coverage()
        row = next(r for r in coverage if r["symbol"] == SYMBOL)
        from tradesome.core.types import BacktestConfig
        return BacktestConfig(symbols=tuple(symbols), start=row["first"].date(),
                              end=row["last"].date(), strategy_key="ma_cross",
                              strategy_params={"fast": 5, "slow": 20,
                                               "trend": 0, "weight": 0.2},
                              initial_cash=100_000.0)

    def test_backtest_end_to_end(self) -> None:
        run_id = self.services.backtest.start(self.make_config())
        self.assertTrue(run_id.startswith("BT-"))
        self.services.backtest.join(60)
        self.assertFalse(self.services.backtest.is_running())
        result = self.services.backtest.latest_result()
        self.assertIsNotNone(result)
        self.assertEqual(result.run_id, run_id)
        self.assertIn("total_return", result.metrics)
        self.assertAlmostEqual(self.services.backtest.progress, 100.0, places=1)
        runs = self.services.backtest.history()
        self.assertTrue(any(r["run_id"] == run_id for r in runs))
        loaded = self.services.backtest.load(run_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, run_id)

    def test_backtest_without_data_raises(self) -> None:
        with self.assertRaises(DataError):
            self.services.backtest.start(self.make_config(symbols=("000000.XX",)))

    def test_report_exports(self) -> None:
        run_id = self.services.backtest.start(self.make_config())
        self.services.backtest.join(60)
        result = self.services.backtest.latest_result()
        markdown = self.services.report.build_markdown(result)
        self.assertIn("TradeSome", markdown)
        self.assertIn("核心指标", markdown)
        path = self.services.report.save_markdown(result)
        self.assertTrue(Path(path).exists())
        exported = self.services.report.export_all(result)
        self.assertTrue(Path(exported["markdown"]).exists())
        self.assertTrue(Path(exported["equity"]).exists())


class LiveServiceTest(ServiceTestCase):
    def test_live_session_lifecycle(self) -> None:
        session_id = self.services.live.start("ma_cross", speed=20.0, days=250,
                                              initial_cash=100_000.0)
        self.assertTrue(session_id.startswith("LIVE-"))
        self.assertTrue(self.services.live.is_running())
        status = self.services.live.status()
        self.assertIn(status["status"], ("running", "paused"))
        self.services.live.pause()
        self.assertEqual(self.services.live.status()["status"], "paused")
        self.services.live.resume()
        self.assertEqual(self.services.live.status()["status"], "running")
        self.assertTrue(wait_until(lambda: not self.services.live.is_running(),
                                   timeout=40))
        self.services.live.stop()
        curve = self.services.live.equity_curve()
        self.assertGreater(len(curve), 10)
        state = self.services.live.risk_state()
        self.assertIn("kill_switch", state)
        self.assertIsInstance(self.services.live.positions(), list)
        self.assertIsInstance(self.services.live.orders(), list)

    def test_stop_without_start_is_safe(self) -> None:
        self.services.live.stop()
        self.assertFalse(self.services.live.is_running())


if __name__ == "__main__":
    unittest.main()
