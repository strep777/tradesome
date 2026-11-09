"""策略工坊：档案校验、动态策略、风控叠加层与持久化。"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from tests.logging_utils import close_log_handlers
from tests.support import TempWorkspace, make_bars
from tradesome.core.errors import ValidationError
from tradesome.core.types import Bar, Position
from tradesome.strategy.profile import (ProfileSpec, make_profile_class,
                                        validate_profile)

from tradesome.app.bootstrap import build_container, shutdown


class FakeContext:
    def __init__(self, closes=None, position=None):
        self.closes = list(closes or [])
        self.position = position
        self.closed = []
        self.buy_filter = None

    def history(self, symbol, n=None, field="close"):
        return list(self.closes[-n:]) if n else list(self.closes)

    def position_of(self, symbol):
        return self.position

    def close(self, symbol, reason=""):
        self.closed.append((symbol, reason))
        return None


def _bar(close=10.0, symbol="600519.SH"):
    return Bar(symbol=symbol, dt=datetime(2024, 1, 2, 15, 0), open=close,
               high=close * 1.01, low=close * 0.99, close=close, volume=1_000_000)


class ProfileValidateTest(unittest.TestCase):
    def setUp(self):
        self.c = build_container(memory_db=True, with_demo=False)
        self.registry = self.c.strategies

    def tearDown(self):
        shutdown(self.c)

    def test_valid_spec(self):
        spec = validate_profile({
            "name": "我的均线", "template": "ma_cross",
            "params": {"fast": 5, "slow": 20, "weight": 0.2},
            "stop_loss_pct": 4, "take_profit_pct": 12, "trend_ma": 60,
        }, self.registry)
        self.assertTrue(spec.key.startswith("custom_"))
        self.assertEqual(spec.params["fast"], 5)
        self.assertEqual(spec.stop_loss_pct, 4.0)

    def test_bad_name_and_template(self):
        with self.assertRaises(ValidationError):
            validate_profile({"name": "x", "template": "ma_cross"}, self.registry)
        with self.assertRaises(ValidationError):
            validate_profile({"name": "策略", "template": "nope"}, self.registry)
        with self.assertRaises(ValidationError):
            validate_profile({"name": "策略", "template": ""}, self.registry)

    def test_overlay_bounds(self):
        base = {"name": "策略", "template": "ma_cross"}
        with self.assertRaises(ValidationError):
            validate_profile(dict(base, stop_loss_pct=60), self.registry)
        with self.assertRaises(ValidationError):
            validate_profile(dict(base, trend_ma=3), self.registry)
        spec = validate_profile(dict(base, trend_ma=0, stop_loss_pct=0),
                                self.registry)
        self.assertEqual(spec.trend_ma, 0)

    def test_params_coerced(self):
        spec = validate_profile({"name": "策略", "template": "ma_cross",
                                 "params": {"fast": "8"}}, self.registry)
        self.assertEqual(spec.params["fast"], 8)


class ProfileStrategyTest(unittest.TestCase):
    def setUp(self):
        self.c = build_container(memory_db=True, with_demo=False)
        self.base = self.c.strategies._class("ma_cross")
        self.spec = ProfileSpec(
            profile_id="abc123", name="测试策略", template="ma_cross",
            params={"fast": 5, "slow": 20, "weight": 0.2},
            stop_loss_pct=5, take_profit_pct=20, trend_ma=10)

    def tearDown(self):
        shutdown(self.c)

    def test_class_meta_and_params(self):
        cls = make_profile_class(self.spec, self.base)
        inst = cls()
        self.assertEqual(cls.meta.name, "测试策略")
        self.assertTrue(cls.meta.key.startswith("custom_"))
        self.assertEqual(inst.p["fast"], 5)
        self.assertEqual(inst.p["risk_stop_pct"], 5.0)
        names = [spec.name for spec in inst.param_specs()]
        self.assertIn("risk_trend_ma", names)
        self.assertIn("fast", names)

    def test_trend_filter_blocks_buys(self):
        cls = make_profile_class(self.spec, self.base)
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 11 + [8.0])
        inst.bind(ctx)
        inst.on_start()
        self.assertTrue(ctx.buy_filter("600519.SH"))
        inst._blocked = inst._trend_blocked(_bar(close=8.0))
        self.assertFalse(ctx.buy_filter("600519.SH"))

    def test_stop_loss_closes_position(self):
        cls = make_profile_class(self.spec, self.base)
        inst = cls()
        position = Position(symbol="600519.SH", qty=100, available_qty=100,
                            avg_cost=10.0, last_price=10.0)
        ctx = FakeContext(closes=[10.0] * 20, position=position)
        inst.bind(ctx)
        inst.on_start()
        inst._check_exit(_bar(close=9.4))
        self.assertEqual(ctx.closed, [("600519.SH", "止损 5.0%")])

    def test_take_profit_closes_position(self):
        cls = make_profile_class(self.spec, self.base)
        inst = cls()
        position = Position(symbol="600519.SH", qty=100, available_qty=100,
                            avg_cost=10.0, last_price=10.0)
        ctx = FakeContext(closes=[10.0] * 20, position=position)
        inst.bind(ctx)
        inst.on_start()
        inst._check_exit(_bar(close=12.1))
        self.assertEqual(ctx.closed, [("600519.SH", "止盈 20.0%")])


class ProfileServiceTest(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.c = build_container(root=self.ws.root, memory_db=False,
                                 with_demo=False)
        self.svc = self.c.services.strategy

    def tearDown(self):
        shutdown(self.c)
        close_log_handlers()
        self.c = None
        self.ws.cleanup()

    def test_save_create_and_delete(self):
        payload = {"name": "双均线实例", "template": "ma_cross",
                   "params": {"fast": 6, "slow": 30, "weight": 0.2},
                   "stop_loss_pct": 3, "take_profit_pct": 15, "trend_ma": 60}
        saved = self.svc.save_profile(payload)
        key = saved["key"]
        self.assertIn(key, self.c.strategies)
        self.assertTrue(self.c.strategies.is_profile(key))
        meta = self.svc.meta(key)
        self.assertEqual(meta["name"], "双均线实例")
        self.assertTrue(meta["is_profile"])
        self.assertEqual(self.svc.defaults(key)["fast"], 6)
        inst = self.svc.create(key)
        self.assertEqual(inst.p["slow"], 30)
        self.assertEqual(inst.p["risk_trend_ma"], 60)

        self.svc.delete_profile(saved["profile_id"])
        self.assertNotIn(key, self.c.strategies)
        self.assertEqual(self.svc.profiles(), [])

    def test_saved_profile_survives_restart(self):
        saved = self.svc.save_profile({
            "name": "重启后还在", "template": "rsi_reversal",
            "params": {"period": 10}, "stop_loss_pct": 0,
            "take_profit_pct": 0, "trend_ma": 0})
        key = saved["key"]
        shutdown(self.c)
        self.c = build_container(root=self.ws.root, memory_db=False,
                                 with_demo=False)
        self.svc = self.c.services.strategy
        self.assertIn(key, self.c.strategies)
        meta = self.svc.meta(key)
        self.assertEqual(meta["name"], "重启后还在")
        self.assertEqual(self.svc.defaults(key)["period"], 10)

    def test_edit_keeps_profile_id(self):
        saved = self.svc.save_profile({"name": "旧名字", "template": "ma_cross"})
        updated = self.svc.save_profile(
            {"name": "新名字", "template": "ma_cross",
             "params": {"fast": 9, "slow": 40, "weight": 0.1},
             "stop_loss_pct": 8, "take_profit_pct": 25, "trend_ma": 0},
            profile_id=saved["profile_id"])
        self.assertEqual(updated["key"], saved["key"])
        self.assertEqual(self.svc.meta(updated["key"])["name"], "新名字")
        self.assertEqual(self.svc.defaults(updated["key"])["fast"], 9)

    def test_template_choices_exclude_profiles(self):
        saved = self.svc.save_profile({"name": "实例", "template": "ma_cross"})
        keys = [row["key"] for row in self.svc.template_choices()]
        self.assertNotIn(saved["key"], keys)
        self.assertIn("ma_cross", keys)


if __name__ == "__main__":
    unittest.main()
