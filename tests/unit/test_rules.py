"""自由组合策略：校验、条件求值、退出规则与服务层。"""

from __future__ import annotations

import unittest
from datetime import datetime

from tests.logging_utils import close_log_handlers
from tests.support import TempWorkspace
from tradesome.app.bootstrap import build_container, shutdown
from tradesome.core.errors import ValidationError
from tradesome.core.types import Bar, Position
from tradesome.strategy.profile import ProfileSpec, make_rule_class
from tradesome.strategy.rules import (default_rules, describe_partial,
                                      summarize_rules, validate_rules)


def _bar(close=10.0, volume=1_000_000, symbol="600519.SH"):
    return Bar(symbol=symbol, dt=datetime(2024, 1, 2, 15, 0), open=close,
               high=max(close, 10.0) * 1.01, low=min(close, 10.0) * 0.99,
               close=close, volume=volume)


class FakeContext:
    def __init__(self, closes=None, highs=None, volumes=None, position=None):
        self.closes = list(closes or [])
        self.highs = list(highs or [])
        self.volumes = list(volumes or [])
        self.position = position
        self.buys = []
        self.closed = []

    def history(self, symbol, n=None, field="close"):
        data = {"close": self.closes, "high": self.highs,
                "volume": self.volumes}.get(field, [])
        return list(data[-n:]) if n else list(data)

    def position_of(self, symbol):
        return self.position

    def buy(self, symbol, weight=None, qty=None, limit_price=None, reason=""):
        self.buys.append((symbol, weight, reason))
        return object()

    def close(self, symbol, reason="策略平仓"):
        self.closed.append((symbol, reason))
        return object()


def _spec(rules):
    return validate_rules(rules)


class RulesValidateTest(unittest.TestCase):
    def test_defaults_and_summary(self):
        rules = default_rules()
        self.assertEqual(rules["match"], "all")
        self.assertEqual(len(rules["entry"]), 1)
        text = summarize_rules(rules)
        self.assertIn("买入", text)
        self.assertIn("止损 5", text)
        self.assertIn("仓位", text)

    def test_requires_entry_and_exit(self):
        with self.assertRaises(ValidationError):
            validate_rules({"entry": []})
        with self.assertRaises(ValidationError):
            validate_rules({"entry": [{"type": "breakout"}],
                            "exit": {"stop_pct": 0, "take_pct": 0,
                                     "ma_period": 0, "max_hold_days": 0}})

    def test_ma_fast_must_be_less_than_slow(self):
        with self.assertRaises(ValidationError):
            validate_rules({"entry": [{"type": "ma_cross", "fast": 30, "slow": 10}],
                            "exit": {"stop_pct": 5}})

    def test_too_many_rules_and_bad_weight(self):
        entry = [{"type": "breakout"}] * 5
        with self.assertRaises(ValidationError):
            validate_rules({"entry": entry, "exit": {"stop_pct": 5}})
        with self.assertRaises(ValidationError):
            validate_rules({"entry": [{"type": "breakout"}],
                            "exit": {"stop_pct": 5}, "weight": 0.9})

    def test_partial_description_tolerates_garbage(self):
        text = describe_partial([{"type": "ma_cross", "fast": "x"}],
                                "all", {}, 0.2)
        self.assertIsInstance(text, str)


class RulesClassTest(unittest.TestCase):
    def _class(self, entry=None, exit=None, match="all"):
        rules = _spec({"match": match,
                       "entry": entry or [{"type": "breakout", "period": 10}],
                       "exit": exit or {"stop_pct": 5}})
        spec = ProfileSpec(profile_id="abc123", name="组合测试",
                           template="rules", kind="rules", rules=rules)
        return make_rule_class(spec)

    def test_meta_and_params(self):
        cls = self._class()
        inst = cls()
        self.assertTrue(cls.meta.key.startswith("custom_abc123"))
        self.assertEqual([s.name for s in inst.param_specs()], ["weight"])
        self.assertAlmostEqual(inst.p["weight"], 0.2)

    def test_breakout_matches_and_buys(self):
        cls = self._class()
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 30, highs=[10.0] * 10 + [11.0],
                          position=None)
        inst.bind(ctx)
        inst.on_bar(_bar(close=12.0))
        self.assertEqual(len(ctx.buys), 1)
        self.assertEqual(ctx.buys[0][2], "组合条件")
        inst.on_bar(_bar(close=12.0))
        self.assertEqual(len(ctx.buys), 1, "同一根之后不应连续加仓")

    def test_ma_cross_and_any_match(self):
        cls = self._class(entry=[{"type": "ma_cross", "fast": 2, "slow": 5},
                                 {"type": "momentum", "period": 5, "pct": 50}],
                          match="any")
        inst = cls()
        closes = [10.0] * 6 + [12.0]
        ctx = FakeContext(closes=closes, position=None)
        inst.bind(ctx)
        inst.on_bar(_bar(close=12.0))
        self.assertEqual(len(ctx.buys), 1)

    def test_momentum_boll_and_volume(self):
        cls = self._class(entry=[{"type": "momentum", "period": 5, "pct": 3}])
        inst = cls()
        ctx = FakeContext(closes=[100.0] * 6, position=None)
        inst.bind(ctx)
        inst.on_bar(_bar(close=110.0))
        self.assertEqual(len(ctx.buys), 1)

        cls = self._class(entry=[{"type": "boll_low", "period": 5, "k": 2.0}])
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 5, position=None)
        inst.bind(ctx)
        inst.on_bar(_bar(close=5.0))
        self.assertEqual(len(ctx.buys), 1)

        cls = self._class(entry=[{"type": "volume_surge", "period": 5,
                                  "ratio": 2.0}])
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 6, volumes=[100.0] * 6,
                          position=None)
        inst.bind(ctx)
        inst.on_bar(_bar(close=10.0, volume=300))
        self.assertEqual(len(ctx.buys), 1)

    def test_exit_rules(self):
        pos = Position(symbol="600519.SH", qty=100, available_qty=100,
                       avg_cost=10.0, last_price=10.0)
        cls = self._class(exit={"stop_pct": 5})
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 10, position=pos)
        inst.bind(ctx)
        inst.on_bar(_bar(close=9.4))
        self.assertEqual(ctx.closed[0][1], "止损 5.0%")

        cls = self._class(exit={"take_pct": 20})
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 10, position=pos)
        inst.bind(ctx)
        inst.on_bar(_bar(close=12.5))
        self.assertEqual(ctx.closed[0][1], "止盈 20.0%")

        cls = self._class(exit={"max_hold_days": 2})
        inst = cls()
        ctx = FakeContext(closes=[10.0] * 10, position=pos)
        inst.bind(ctx)
        inst.on_bar(_bar(close=10.0))
        inst.on_bar(_bar(close=10.0))
        self.assertTrue(ctx.closed)
        self.assertIn("持有", ctx.closed[0][1])


class RuleServiceTest(unittest.TestCase):
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

    def test_save_rules_profile(self):
        payload = {"kind": "rules", "name": "我的突破组合",
                   "rules": {"match": "all",
                             "entry": [{"type": "breakout", "period": 20}],
                             "exit": {"stop_pct": 6, "take_pct": 15,
                                      "ma_period": 0, "max_hold_days": 0},
                             "weight": 0.15}}
        saved = self.svc.save_profile(payload)
        key = saved["key"]
        self.assertIn(key, self.c.strategies)
        meta = self.svc.meta(key)
        self.assertEqual(meta["kind"], "rules")
        self.assertIn("突破", meta["description"])
        self.assertEqual(self.svc.defaults(key), {"weight": 0.15})
        inst = self.svc.create(key)
        self.assertAlmostEqual(inst.p["weight"], 0.15)
        self.svc.delete_profile(saved["profile_id"])
        self.assertNotIn(key, self.c.strategies)


if __name__ == "__main__":
    unittest.main()
