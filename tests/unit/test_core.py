"""核心层测试：日历、金额、类型、事件总线、配置、编号与标的。"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from tradesome.core import calendar
from tradesome.core.bus import ALL, EventBus
from tradesome.core.config import (AppConfig, config_from_dict, default_config,
                                   load_config, save_config)
from tradesome.core.errors import ConfigError, ValidationError
from tradesome.core.ids import (client_order_id, new_order_id, new_run_id,
                                new_session_id, short_id)
from tradesome.core.instruments import (InstrumentRegistry, normalize_symbol,
                                        round_qty, validate_qty)
from tradesome.core.money import (change_pct, clamp, floor_price, pct,
                                  round_money, round_price, safe_div)
from tradesome.core.types import Bar, FeeProfile, Signal
from tests.support import TempWorkspace


class CalendarTest(unittest.TestCase):
    def test_weekend_is_not_trading_day(self) -> None:
        self.assertFalse(calendar.is_trading_day(date(2025, 1, 4)))  # 周六
        self.assertFalse(calendar.is_trading_day(date(2025, 1, 5)))  # 周日
        self.assertTrue(calendar.is_trading_day(date(2025, 1, 6)))

    def test_new_year_holiday(self) -> None:
        self.assertFalse(calendar.is_trading_day(date(2025, 1, 1)))

    def test_trading_days_are_sorted_and_unique(self) -> None:
        days = calendar.trading_days(date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(days, sorted(set(days)))
        self.assertTrue(all(calendar.is_trading_day(d) for d in days))
        self.assertEqual(calendar.count_trading_days(date(2025, 1, 1),
                                                    date(2025, 1, 31)), len(days))

    def test_previous_and_next_trading_day(self) -> None:
        saturday = date(2025, 1, 4)
        self.assertEqual(calendar.previous_trading_day(saturday), date(2025, 1, 3))
        self.assertEqual(calendar.next_trading_day(saturday), date(2025, 1, 6))
        self.assertEqual(calendar.previous_trading_day(date(2025, 1, 3)),
                         date(2025, 1, 2))

    def test_session_phase(self) -> None:
        morning = datetime(2025, 1, 6, 10, 0)
        noon = datetime(2025, 1, 6, 12, 0)
        afternoon = datetime(2025, 1, 6, 14, 0)
        after = datetime(2025, 1, 6, 16, 0)
        self.assertTrue(calendar.is_market_open(morning))
        self.assertFalse(calendar.is_market_open(noon))
        self.assertTrue(calendar.is_market_open(afternoon))
        self.assertFalse(calendar.is_market_open(after))
        self.assertNotEqual(calendar.session_phase(morning), calendar.session_phase(noon))
        self.assertTrue(calendar.phase_label(calendar.session_phase(morning)))

    def test_trading_days_between_datetimes(self) -> None:
        days = calendar.trading_days_between(datetime(2025, 1, 6, 9, 30),
                                             datetime(2025, 1, 10, 15, 0))
        self.assertEqual(days[0], date(2025, 1, 6))
        self.assertEqual(days[-1], date(2025, 1, 10))


class MoneyTest(unittest.TestCase):
    def test_round_money_half_up(self) -> None:
        self.assertEqual(round_money(1.005), 1.01)
        self.assertEqual(round_money(-1.005), -1.01)
        self.assertEqual(round_money(2.344), 2.34)

    def test_price_rounding_respects_tick(self) -> None:
        self.assertEqual(round_price(10.006), 10.01)
        self.assertEqual(floor_price(10.009), 10.0)
        self.assertEqual(round_price(10.03, tick=0.05), 10.05)

    def test_pct_and_change(self) -> None:
        self.assertAlmostEqual(pct(25, 100), 0.25)
        self.assertEqual(pct(1, 0), 0.0)
        self.assertAlmostEqual(change_pct(11, 10), 0.1)
        self.assertEqual(change_pct(10, 0), 0.0)
        self.assertEqual(safe_div(1, 0, default=-1), -1)
        self.assertEqual(clamp(5, 0, 3), 3)


class TypesTest(unittest.TestCase):
    def test_bar_rejects_inconsistent_ohlc(self) -> None:
        with self.assertRaises(Exception):
            Bar(symbol="600519.SH", dt=datetime(2025, 1, 6), open=10,
                high=9, low=8, close=9.5)

    def test_bar_rejects_negative_price(self) -> None:
        with self.assertRaises(Exception):
            Bar(symbol="600519.SH", dt=datetime(2025, 1, 6), open=-1, high=1,
                low=-1, close=1)

    def test_bar_to_dict_contains_symbol_and_datetime(self) -> None:
        bar = Bar(symbol="600519.SH", dt=datetime(2025, 1, 6), open=10,
                  high=11, low=9, close=10.5, volume=100, amount=1000)
        data = bar.to_dict()
        self.assertEqual(data["symbol"], "600519.SH")
        self.assertIn("dt", data)

    def test_signal_action_normalized(self) -> None:
        signal = Signal(symbol="600519.SH", dt=datetime(2025, 1, 6), action="BUY")
        self.assertEqual(signal.action, "BUY")
        with self.assertRaises(ValidationError):
            Signal(symbol="600519.SH", dt=datetime(2025, 1, 6), action="buy")

    def test_fee_profile_validates_negative(self) -> None:
        with self.assertRaises(Exception):
            FeeProfile(commission_rate=-0.1)


class InstrumentTest(unittest.TestCase):
    def test_normalize_symbol(self) -> None:
        self.assertEqual(normalize_symbol("600519"), "600519.SH")
        self.assertEqual(normalize_symbol("000001"), "000001.SZ")
        self.assertEqual(normalize_symbol("600519.sh"), "600519.SH")

    def test_instrument_limits(self) -> None:
        registry = InstrumentRegistry.with_defaults()
        star = registry.must_get("688981.SH")
        main = registry.must_get("600519.SH")
        self.assertEqual(star.price_limit_pct, 0.2)
        self.assertEqual(main.price_limit_pct, 0.1)
        self.assertTrue(main.name)

    def test_round_qty_to_lot(self) -> None:
        registry = InstrumentRegistry.with_defaults()
        inst = registry.must_get("600519.SH")
        self.assertEqual(round_qty(inst, 1234), 1200)
        self.assertEqual(round_qty(inst, 99), 0)

    def test_validate_qty(self) -> None:
        registry = InstrumentRegistry.with_defaults()
        inst = registry.must_get("600519.SH")
        ok, message = validate_qty(inst, 300)
        self.assertTrue(ok, message)
        ok, message = validate_qty(inst, 0)
        self.assertFalse(ok)
        ok, message = validate_qty(inst, 150)
        self.assertFalse(ok)
        self.assertIn("100", message)

    def test_registry_search_and_display(self) -> None:
        registry = InstrumentRegistry.with_defaults()
        hits = registry.search("茅台")
        self.assertTrue(hits)
        self.assertIn("600519.SH", [h.symbol for h in hits])
        self.assertIn("600519.SH", registry.display("600519.SH"))


class IdsTest(unittest.TestCase):
    def test_ids_unique_and_prefixed(self) -> None:
        ids = {new_order_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)
        self.assertTrue(new_order_id().startswith("ORD-"))
        self.assertTrue(new_run_id().startswith("BT-"))
        self.assertTrue(new_session_id().startswith("LIVE-"))

    def test_client_order_id_is_stable(self) -> None:
        dt = datetime(2025, 1, 6, 9, 30)
        first = client_order_id("ma", "600519.SH", "BUY", dt, 1)
        second = client_order_id("ma", "600519.SH", "BUY", dt, 1)
        third = client_order_id("ma", "600519.SH", "BUY", dt, 2)
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_short_id_length(self) -> None:
        self.assertEqual(len(short_id(8)), 8)


class BusTest(unittest.TestCase):
    def test_publish_reaches_topic_and_all(self) -> None:
        bus = EventBus()
        seen = []
        everything = []
        bus.subscribe("demo.topic", seen.append)
        bus.subscribe(ALL, everything.append)
        bus.emit("demo.topic", value=1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["value"], 1)
        self.assertEqual(len(everything), 1)

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        hits = []
        off = bus.subscribe("x", hits.append)
        bus.emit("x")
        off()
        bus.emit("x")
        self.assertEqual(len(hits), 1)

    def test_handler_errors_do_not_break_others(self) -> None:
        bus = EventBus()
        received = []

        def boom(_event):
            raise RuntimeError("handler failed")

        bus.subscribe("y", boom)
        bus.subscribe("y", received.append)
        bus.emit("y")
        self.assertEqual(len(received), 1)

    def test_history_is_bounded(self) -> None:
        bus = EventBus(history=5)
        for index in range(12):
            bus.emit("n", index=index)
        recent = bus.recent(limit=100)
        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[-1].payload["index"], 11)
        self.assertEqual(recent[-1].seq, 12)

    def test_recent_filters_topic(self) -> None:
        bus = EventBus()
        bus.emit("a")
        bus.emit("b")
        self.assertEqual(len(bus.recent(topic="a")), 1)


class ConfigTest(unittest.TestCase):
    def test_defaults(self) -> None:
        config = default_config()
        self.assertEqual(config.trading.initial_cash, 100000.0)
        self.assertTrue(config.risk.enabled_rules)
        self.assertEqual(config.risk.max_drawdown_halt, 0.2)

    def test_roundtrip_save_load(self) -> None:
        with TempWorkspace() as tmp:
            config = default_config()
            config.appearance.theme = "light"
            config.trading.initial_cash = 250000
            path = save_config(config, tmp.root / "config.json")
            loaded, warnings = load_config(path)
            self.assertEqual(loaded.appearance.theme, "light")
            self.assertEqual(loaded.trading.initial_cash, 250000)
            self.assertEqual(warnings, [])

    def test_unknown_and_invalid_fields_fall_back(self) -> None:
        raw = {
            "schema": 1,
            "appearance": {"theme": "neon", "font_scale": 9},
            "trading": {"initial_cash": -5},
            "mystery": {"a": 1},
        }
        config, warnings = config_from_dict(raw)
        self.assertEqual(config.appearance.theme, "macos")
        self.assertLessEqual(config.appearance.font_scale, 1.6)
        self.assertGreater(config.trading.initial_cash, 0)
        self.assertTrue(warnings)

    def test_risk_rules_kept_within_range(self) -> None:
        config, _ = config_from_dict({"risk": {"single_trade_pct": 5.0}})
        self.assertLessEqual(config.risk.single_trade_pct, 1.0)

    def test_load_missing_file_returns_defaults(self) -> None:
        with TempWorkspace() as tmp:
            config, warnings = load_config(tmp.root / "nope.json")
            self.assertIsInstance(config, AppConfig)
            self.assertIsInstance(warnings, list)

    def test_corrupt_file_returns_defaults(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "config.json"
            path.write_text("{not json", encoding="utf-8")
            config, warnings = load_config(path)
            self.assertIsInstance(config, AppConfig)
            self.assertTrue(warnings)

    def test_config_error_type(self) -> None:
        self.assertTrue(issubclass(ConfigError, Exception))
        self.assertTrue(issubclass(ValidationError, Exception))

    def test_backtest_config_evolve(self) -> None:
        from tradesome.core.types import BacktestConfig
        config = BacktestConfig(symbols=("600519.SH",), start=date(2022, 1, 1),
                                end=date(2022, 12, 31))
        changed = config.evolve(initial_cash=50000)
        self.assertEqual(changed.initial_cash, 50000)
        self.assertEqual(config.initial_cash, 100000)


if __name__ == "__main__":
    unittest.main()
