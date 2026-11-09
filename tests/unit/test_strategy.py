"""策略层测试：指标、参数、注册表、内置策略与用户策略静态检查。"""

from __future__ import annotations

import math
import unittest

from tradesome.core.errors import StrategyError, StrategyLoadError, ValidationError
from tradesome.strategy.base import ParamSpec, coerce_params, default_params
from tradesome.strategy.registry import StrategyRegistry
from tradesome.strategy.sandbox import (check_source, execute_source,
                                        extract_strategy)
from tradesome.strategy.indicators import (atr, boll, ema, highest, lowest,
                                           macd, rsi, sma, volatility)
from tradesome.strategy.base import crossed_down, crossed_up, last
from tests.support import TempWorkspace, make_bars

USER_SOURCE = '''
from tradesome.strategy.base import ParamSpec, Strategy, StrategyMeta


class DualMA(Strategy):
    meta = StrategyMeta(
        key="dual_ma_test", name="双均线（测试）", category="趋势",
        description="快线上穿慢线买入，下穿卖出。",
    )

    def param_specs(self):
        return [
            ParamSpec("fast", "快线周期", "int", 5, 2, 30, "日"),
            ParamSpec("slow", "慢线周期", "int", 20, 10, 120, "日"),
        ]

    def on_bar(self, bar):
        fast = last(self.indicator.sma(bar.symbol, self.params["fast"]))
        slow = last(self.indicator.sma(bar.symbol, self.params["slow"]))
        if fast is None or slow is None:
            return
        if crossed_up([fast], [slow]) and not self.position_of(bar.symbol):
            self.buy(bar.symbol, weight=0.9)
        elif crossed_down([fast], [slow]):
            self.close(bar.symbol)
'''


class IndicatorTest(unittest.TestCase):
    def test_sma_and_ema_on_constant(self) -> None:
        values = [10.0] * 30
        self.assertEqual(sma(values, 5)[-1], 10.0)
        self.assertEqual(ema(values, 5)[-1], 10.0)
        self.assertTrue(math.isnan(sma(values, 5)[0]))
        self.assertTrue(math.isnan(sma(values, 5)[3]))
        self.assertEqual(sma(values, 5)[4], 10.0)

    def test_rsi_bounds(self) -> None:
        up = [float(i) for i in range(1, 60)]
        down = [float(i) for i in range(60, 1, -1)]
        self.assertGreater(rsi(up)[-1], 70)
        self.assertLess(rsi(down)[-1], 30)

    def test_boll_bands_ordered(self) -> None:
        bars = make_bars(days=80)
        closes = [b.close for b in bars]
        middle, upper, lower = boll(closes, 20, 2.0)
        self.assertGreater(upper[-1], middle[-1])
        self.assertGreater(middle[-1], lower[-1])

    def test_atr_positive(self) -> None:
        bars = make_bars(days=60)
        values = atr(bars, 14)
        self.assertGreater(values[-1], 0)
        self.assertTrue(math.isnan(values[0]))

    def test_macd_returns_triple(self) -> None:
        values = [b.close for b in make_bars(days=80)]
        diff, dea, hist = macd(values)
        self.assertEqual(len(diff), len(values))
        self.assertEqual(len(dea), len(values))
        self.assertAlmostEqual(hist[-1], (diff[-1] - dea[-1]) * 2, places=6)

    def test_highest_lowest(self) -> None:
        values = [5, 1, 9, 3, 7]
        self.assertEqual(highest(values, 3)[-1], 9)
        self.assertEqual(lowest(values, 3)[-1], 3)

    def test_volatility_non_negative(self) -> None:
        values = [b.close for b in make_bars(days=60)]
        self.assertGreaterEqual(volatility(values, 20)[-1], 0)

    def test_cross_helpers(self) -> None:
        self.assertTrue(crossed_up([1.0, 3.0], [2.0, 2.0]))
        self.assertFalse(crossed_up([3.0, 3.0], [2.0, 2.0]))
        self.assertTrue(crossed_down([3.0, 1.0], [2.0, 2.0]))
        self.assertIsNone(last([]))
        self.assertEqual(last([1.0, 2.0]), 2.0)


class ParamSpecTest(unittest.TestCase):
    def test_int_coercion_and_out_of_range(self) -> None:
        spec = ParamSpec("period", "周期", "int", 20, 2, 60, "日")
        self.assertEqual(spec.coerce("30"), 30)
        self.assertEqual(spec.default_value(), 20)
        with self.assertRaises(ValidationError):
            spec.coerce(1)
        with self.assertRaises(ValidationError):
            spec.coerce(1000)

    def test_float_and_bool(self) -> None:
        spec = ParamSpec("ratio", "比例", "float", 0.5, 0.1, 1.0)
        self.assertAlmostEqual(spec.coerce("0.8"), 0.8)
        flag = ParamSpec("flag", "开关", "bool", True)
        self.assertFalse(flag.coerce("false"))
        self.assertFalse(flag.coerce(0))
        self.assertTrue(flag.coerce("是"))

    def test_choice(self) -> None:
        spec = ParamSpec("mode", "模式", "choice", "a", choices=("a", "b"))
        self.assertEqual(spec.coerce("b"), "b")
        with self.assertRaises(ValidationError):
            spec.coerce("zzz")

    def test_str(self) -> None:
        spec = ParamSpec("text", "文本", "str", "hello")
        self.assertEqual(spec.coerce("  world  "), "  world  ")
        # None 视为“未填”，coerce_params 会回落到默认值。
        self.assertEqual(coerce_params([spec], {"text": None})["text"], "hello")

    def test_invalid_type_raises(self) -> None:
        with self.assertRaises(Exception):
            ParamSpec("x", "x", "unknown", 1)

    def test_coerce_params_ignores_unknown(self) -> None:
        specs = [ParamSpec("period", "周期", "int", 20, 2, 60)]
        result = coerce_params(specs, {"period": 30, "unknown": 1})
        self.assertEqual(result["period"], 30)
        self.assertNotIn("unknown", result)
        self.assertEqual(default_params(specs)["period"], 20)


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = TempWorkspace()
        self.user_dir = self.ws.root / "strategies"
        self.user_dir.mkdir()
        self.registry = StrategyRegistry(user_dir=str(self.user_dir))

    def tearDown(self) -> None:
        self.ws.cleanup()

    def test_builtins_registered(self) -> None:
        keys = self.registry.keys()
        for expected in ("ma_cross", "boll_reversion", "rsi_reversal",
                         "grid", "momentum_rotation", "sentiment_boost"):
            self.assertIn(expected, keys)
        self.assertGreaterEqual(len(keys), 6)
        self.assertEqual(self.registry.errors(), [])

    def test_meta_fields(self) -> None:
        meta = self.registry.meta("ma_cross")
        self.assertTrue(meta.name)
        self.assertTrue(meta.description)
        self.assertTrue(meta.category)

    def test_params_and_defaults(self) -> None:
        specs = self.registry.params("ma_cross")
        self.assertTrue(specs)
        values = self.registry.default_params("ma_cross")
        self.assertEqual(set(values), {s.name for s in specs})
        for spec in specs:
            self.assertIn(spec.name, values)

    def test_create_with_overrides(self) -> None:
        strategy = self.registry.create("ma_cross", {"fast": 3, "slow": 12})
        self.assertEqual(strategy.p["fast"], 3)
        self.assertEqual(strategy.p["slow"], 12)
        self.assertEqual(strategy.meta.key, "ma_cross")

    def test_unknown_key_raises(self) -> None:
        with self.assertRaises(StrategyError):
            self.registry.create("no_such_strategy")

    def test_duplicate_key_rejected(self) -> None:
        cls = type(self.registry.create("ma_cross"))
        with self.assertRaises(StrategyError):
            self.registry.register(cls)

    def test_user_strategy_load_and_reload(self) -> None:
        path = self.user_dir / "dual_ma_test.py"
        path.write_text(USER_SOURCE, encoding="utf-8")
        problems = self.registry.reload_user()
        self.assertEqual(problems, [])
        self.assertIn("dual_ma_test", self.registry.keys())
        self.assertTrue(self.registry.is_user("dual_ma_test"))
        strategy = self.registry.create("dual_ma_test", {"fast": 4})
        self.assertEqual(strategy.p["fast"], 4)

    def test_user_strategy_with_syntax_error_is_reported(self) -> None:
        (self.user_dir / "broken.py").write_text("class :", encoding="utf-8")
        problems = self.registry.reload_user()
        self.assertTrue(problems)
        self.assertNotIn("broken", self.registry.keys())

    def test_remove_user(self) -> None:
        path = self.user_dir / "dual_ma_test.py"
        path.write_text(USER_SOURCE, encoding="utf-8")
        self.registry.reload_user()
        self.registry.remove_user("dual_ma_test")
        self.assertNotIn("dual_ma_test", self.registry.keys())
        self.assertFalse(path.exists())


class SandboxTest(unittest.TestCase):
    def test_valid_source_passes(self) -> None:
        self.assertEqual(check_source(USER_SOURCE), [])

    def test_forbidden_imports(self) -> None:
        for source in ("import os", "import socket", "from subprocess import Popen"):
            with self.subTest(source=source):
                problems = check_source(source)
                self.assertTrue(problems)

    def test_forbidden_calls(self) -> None:
        for source in ("eval('1')", "exec('x=1')", "open('a.txt')",
                       "__import__('os')", "getattr(x, 'y')"):
            with self.subTest(source=source):
                self.assertTrue(check_source(source))

    def test_forbidden_attributes_and_with(self) -> None:
        self.assertTrue(check_source("x = object.__class__"))
        self.assertTrue(check_source("with open('a') as f:\n    pass"))

    def test_syntax_error_reported(self) -> None:
        problems = check_source("def f(:\n    pass")
        self.assertTrue(problems[0].startswith("语法错误"))

    def test_relative_import_rejected(self) -> None:
        self.assertTrue(check_source("from . import x"))

    def test_execute_and_extract(self) -> None:
        namespace = execute_source(USER_SOURCE, "dual_ma_test.py")
        cls = extract_strategy(namespace, "dual_ma_test.py")
        self.assertEqual(cls.meta.key, "dual_ma_test")

    def test_execute_blocks_forbidden_import_at_runtime(self) -> None:
        with self.assertRaises(StrategyLoadError):
            execute_source("import os\nclass X: pass", "x.py")

    def test_class_without_meta_rejected(self) -> None:
        source = "class X:\n    pass\n"
        namespace = execute_source(source, "x.py")
        with self.assertRaises(StrategyLoadError):
            extract_strategy(namespace, "x.py")


if __name__ == "__main__":
    unittest.main()
