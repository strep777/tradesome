"""回测引擎、绩效指标与滚动验证测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from tradesome.backtest.engine import BacktestEngine
from tradesome.backtest.metrics import (compute_metrics, drawdown_curve,
                                        max_drawdown, monthly_returns)
from tradesome.backtest.walkforward import run_walkforward, split_windows
from tradesome.core.errors import BacktestError
from tradesome.core.instruments import InstrumentRegistry
from tradesome.core.types import BacktestConfig, FeeProfile
from tradesome.strategy.registry import StrategyRegistry
from tests.support import make_bars, make_trend_bars

SYMBOL = "600519.SH"


def day(i: int) -> datetime:
    return datetime(2024, 1, 1) + timedelta(days=i)


class MetricsTest(unittest.TestCase):
    def test_empty_curve(self) -> None:
        metrics = compute_metrics([])
        self.assertEqual(metrics["total_return"], 0.0)
        self.assertEqual(metrics["max_drawdown"], 0.0)
        self.assertEqual(metrics["trade_count"], 0)

    def test_flat_curve(self) -> None:
        curve = [(day(i), 100_000.0) for i in range(30)]
        metrics = compute_metrics(curve, initial_cash=100_000.0)
        self.assertAlmostEqual(metrics["total_return"], 0.0)
        self.assertAlmostEqual(metrics["max_drawdown"], 0.0)
        self.assertEqual(metrics["trade_count"], 0)

    def test_rising_curve(self) -> None:
        curve = [(day(i), 100_000.0 * (1.001 ** i)) for i in range(250)]
        metrics = compute_metrics(curve, initial_cash=100_000.0)
        self.assertAlmostEqual(metrics["total_return"],
                               curve[-1][1] / 100_000.0 - 1.0, places=6)
        self.assertGreater(metrics["annual_return"], 0)

    def test_drawdown_curve_and_max(self) -> None:
        values = [100.0, 120.0, 90.0, 130.0]
        curve = drawdown_curve(values)
        self.assertEqual(curve[0], 0.0)
        self.assertAlmostEqual(curve[2], 0.25)
        worst, longest = max_drawdown(values)
        self.assertAlmostEqual(worst, 0.25)
        self.assertEqual(longest, 1)

    def test_monthly_returns(self) -> None:
        curve = [(datetime(2024, 1, 2), 100.0), (datetime(2024, 1, 31), 110.0),
                 (datetime(2024, 2, 1), 110.0), (datetime(2024, 2, 29), 99.0)]
        result = monthly_returns(curve)
        self.assertAlmostEqual(result["2024-01"], 0.10)
        self.assertAlmostEqual(result["2024-02"], -0.10)


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = StrategyRegistry()
        self.instruments = InstrumentRegistry().with_defaults()

    def config(self, bars, **kwargs) -> BacktestConfig:
        data = dict(symbols=(SYMBOL,), start=bars[0].dt.date(),
                    end=bars[-1].dt.date(), initial_cash=100_000.0,
                    strategy_key="ma_cross",
                    fee=FeeProfile(slippage_bps=0.0), max_volume_ratio=1.0)
        data.update(kwargs)
        return BacktestConfig(**data)

    def engine(self, bars, config=None, key="ma_cross") -> BacktestEngine:
        config = config or self.config(bars)
        strategy = self.registry.create(key, config.strategy_params)
        return BacktestEngine(config, {SYMBOL: bars}, strategy,
                              self.instruments, config.fee)

    def test_run_produces_result(self) -> None:
        bars = make_bars(days=300)
        result = self.engine(bars).run()
        self.assertTrue(result.run_id.startswith("BT-"))
        self.assertFalse(result.cancelled)
        self.assertGreater(len(result.equity_curve), 100)
        self.assertEqual(len(result.equity_curve), len(result.drawdown_curve))
        self.assertIn("total_return", result.metrics)
        self.assertIsNotNone(result.finished_at)
        self.assertGreaterEqual(result.duration_sec, 0)
        self.assertGreater(result.equity_curve[-1][1], 0)

    def test_progress_callback(self) -> None:
        bars = make_bars(days=120)
        seen = []
        self.engine(bars).run(progress=lambda pct, i, total: seen.append(pct))
        self.assertTrue(seen)
        self.assertEqual(seen[-1], 100.0)
        self.assertEqual(seen, sorted(seen))

    def test_cancel_stops_run(self) -> None:
        bars = make_bars(days=400)
        calls = {"n": 0}

        def cancel() -> bool:
            calls["n"] += 1
            return calls["n"] > 3

        result = self.engine(bars).run(cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertLess(len(result.equity_curve), 400)

    def test_no_data_raises(self) -> None:
        bars = make_bars(days=50)
        config = self.config(bars, start=date(2030, 1, 1), end=date(2030, 12, 31))
        with self.assertRaises(BacktestError):
            self.engine(bars, config).run()

    def test_strategy_trades_and_curve(self) -> None:
        bars = make_bars(days=300)
        result = self.engine(bars).run()
        self.assertGreater(result.metrics["trade_count"], 0)
        self.assertGreater(len(result.orders), 0)
        self.assertNotEqual(result.metrics["total_return"], 0)
        self.assertEqual(result.equity_curve[-1][1],
                         result.metrics["total_return"] * 100_000.0 + 100_000.0)

    def test_benchmark_curve(self) -> None:
        bars = make_bars(days=150)
        config = self.config(bars, benchmark=SYMBOL)
        result = self.engine(bars, config).run()
        self.assertTrue(result.benchmark_curve)
        self.assertIsNotNone(result.metrics["benchmark_return"])

    def test_strict_risk_limits_block_trades(self) -> None:
        from tradesome.risk import RiskLimits
        bars = make_bars(days=200)
        config = self.config(bars)
        loose = BacktestEngine(config, {SYMBOL: bars},
                               self.registry.create("ma_cross"),
                               self.instruments, config.fee).run()
        tight = BacktestEngine(config, {SYMBOL: bars},
                               self.registry.create("ma_cross"),
                               self.instruments, config.fee,
                               risk_limits=RiskLimits(single_trade_pct=0.01)).run()
        self.assertGreater(loose.metrics["trade_count"], 0)
        self.assertEqual(tight.metrics["trade_count"], 0)

    def test_two_symbols(self) -> None:
        first = make_bars(symbol="600519.SH", days=160, seed=1)
        second = make_bars(symbol="000001.SZ", days=160, seed=2)
        config = BacktestConfig(symbols=("600519.SH", "000001.SZ"),
                                start=first[0].dt.date(), end=first[-1].dt.date(),
                                strategy_key="ma_cross",
                                fee=FeeProfile(slippage_bps=0.0),
                                max_volume_ratio=1.0)
        engine = BacktestEngine(config, {"600519.SH": first, "000001.SZ": second},
                                self.registry.create("ma_cross"),
                                self.instruments, config.fee)
        result = engine.run()
        self.assertGreater(len(result.equity_curve), 100)


class WalkForwardTest(unittest.TestCase):
    def test_split_windows_cover_range(self) -> None:
        windows = split_windows(date(2020, 1, 1), date(2021, 12, 31),
                                train_days=250, test_days=60, step_days=60)
        self.assertTrue(windows)
        for window in windows:
            self.assertLess(window.train_start, window.train_end)
            self.assertLessEqual(window.train_end, window.test_start)
            self.assertLess(window.test_start, window.test_end)

    def test_run_walkforward(self) -> None:
        bars = make_bars(days=600)
        config = BacktestConfig(symbols=(SYMBOL,), start=bars[0].dt.date(),
                                end=bars[-1].dt.date(), strategy_key="ma_cross",
                                fee=FeeProfile(slippage_bps=0.0),
                                max_volume_ratio=1.0)
        registry = StrategyRegistry()
        result = run_walkforward(
            config, {SYMBOL: bars}, lambda: registry.create("ma_cross"),
            InstrumentRegistry().with_defaults(),
            train_days=120, test_days=60, step_days=90)
        self.assertGreaterEqual(len(result.windows), 1)
        self.assertEqual(len(result.results), len(result.windows))
        self.assertTrue(result.summary)


if __name__ == "__main__":
    unittest.main()
