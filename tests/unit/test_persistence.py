"""SQLite 持久化、迁移与各仓储测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from tradesome.backtest.engine import BacktestEngine
from tradesome.core.instruments import InstrumentRegistry
from tradesome.core.types import (SIDE_BUY, ST_FILLED, BacktestConfig, Bar,
                                  FeeProfile, Fill, Order)
from tradesome.persistence import (SCHEMA_VERSION, Database, current_version,
                                   initialize)
from tradesome.persistence.repositories import Repositories
from tradesome.strategy.registry import StrategyRegistry
from tests.support import TempWorkspace, make_bars

SYMBOL = "600519.SH"
NOW = datetime(2024, 3, 1, 15, 0)


class PersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = TempWorkspace()
        self.db = Database(str(self.ws.root / "market.db"))
        self.db.connect()
        initialize(self.db)
        self.repos = Repositories(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.ws.cleanup()

    # ------------------------------------------------------------ 迁移

    def test_schema_version(self) -> None:
        self.assertEqual(current_version(self.db), SCHEMA_VERSION)
        initialize(self.db)   # 幂等
        self.assertEqual(current_version(self.db), SCHEMA_VERSION)

    # ------------------------------------------------------------ 标的

    def test_instrument_roundtrip(self) -> None:
        registry = InstrumentRegistry().with_defaults()
        items = registry.all()
        self.assertEqual(self.repos.instruments.upsert_many(items), len(items))
        self.assertGreater(self.repos.instruments.count(), 5)
        item = self.repos.instruments.get(SYMBOL)
        self.assertEqual(item.symbol, SYMBOL)
        self.assertEqual(item.name, registry.must_get(SYMBOL).name)
        self.assertIsNone(self.repos.instruments.get("000000.XX"))
        self.assertTrue(self.repos.instruments.delete(SYMBOL))
        self.assertFalse(self.repos.instruments.delete(SYMBOL))

    # ------------------------------------------------------------ 键值

    def test_kv_types(self) -> None:
        kv = self.repos.kv
        kv.set("count", 3)
        kv.set("name", "测试")
        kv.set("nested", {"a": [1, 2], "b": True})
        self.assertEqual(kv.get("count"), 3)
        self.assertEqual(kv.get("name"), "测试")
        self.assertEqual(kv.get("nested")["a"], [1, 2])
        self.assertEqual(kv.get("missing", "fallback"), "fallback")
        kv.set("count", 4)
        self.assertEqual(kv.get("count"), 4)
        self.assertTrue(kv.delete("count"))
        self.assertFalse(kv.delete("count"))
        self.assertEqual(set(kv.all()), {"name", "nested"})
        self.assertEqual(kv.clear(), 2)

    # ------------------------------------------------------------ 审计

    def test_audit_write_and_filter(self) -> None:
        audit = self.repos.audit
        audit.write("order", "submit", detail="提交订单")
        audit.write("order", "reject", detail="拒单", severity="WARNING")
        audit.write("risk", "trip", detail="熔断")
        rows = audit.list_recent(limit=10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["category"], "risk")   # 最新在前
        self.assertEqual(len(audit.list_recent(category="order")), 2)
        self.assertEqual(len(audit.list_recent(severity="WARNING")), 1)
        self.assertEqual(audit.count(), 3)
        self.assertEqual(audit.count(category="order"), 2)

    def test_audit_detail_truncated_and_purged(self) -> None:
        audit = self.repos.audit
        audit.write("data", "import", detail="x" * 5000)
        self.assertEqual(len(audit.list_recent(1)[0]["detail"]), 2000)
        audit.write("data", "old", ts=datetime(2020, 1, 1))
        self.assertEqual(audit.purge_before(datetime(2021, 1, 1)), 1)
        self.assertEqual(audit.clear(), 1)

    # ------------------------------------------------------------ 会话/订单/成交/权益

    def test_session_lifecycle(self) -> None:
        sessions = self.repos.sessions
        sessions.create("LIVE-1", "ma_cross", {"fast": 5},
                        ("600519.SH",), 100_000.0, started_at=NOW)
        row = sessions.get("LIVE-1")
        self.assertEqual(row["strategy_key"], "ma_cross")
        self.assertEqual(row["status"], "RUNNING")
        sessions.update_status("LIVE-1", "PAUSED")
        self.assertEqual(sessions.get("LIVE-1")["status"], "PAUSED")
        sessions.finish("LIVE-1", 101_000.0, status="STOPPED")
        self.assertEqual(sessions.get("LIVE-1")["status"], "STOPPED")
        self.assertIsNotNone(sessions.last())
        self.assertEqual(len(sessions.list_recent()), 1)
        self.assertEqual(sessions.count(), 1)
        self.assertEqual(sessions.clear(), 1)

    def test_orders_and_fills(self) -> None:
        order = Order(order_id="O-1", client_id="c-1", symbol=SYMBOL,
                      side="BUY", qty=100, status=ST_FILLED, filled_qty=100,
                      avg_fill_price=10.0, created_at=NOW, updated_at=NOW,
                      session_id="LIVE-1")
        self.repos.orders.save(order)
        loaded = self.repos.orders.get("O-1")
        self.assertEqual(loaded.symbol, SYMBOL)
        self.assertEqual(loaded.qty, 100)
        self.assertEqual(self.repos.orders.by_client_id("c-1").order_id, "O-1")
        self.assertEqual(len(self.repos.orders.list_by_session("LIVE-1")), 1)
        self.assertEqual(self.repos.orders.count(), 1)
        fill = Fill(fill_id="F-1", order_id="O-1", symbol=SYMBOL,
                    side=SIDE_BUY, qty=100, price=10.0, dt=NOW,
                    commission=5.0)
        self.assertEqual(self.repos.fills.save_many([fill]), 1)
        self.assertEqual(self.repos.fills.list_by_order("O-1")[0].fill_id, "F-1")
        self.assertEqual(self.repos.fills.count(), 1)

    def test_equity_points(self) -> None:
        equity = self.repos.equity
        equity.add("LIVE-1", NOW, 100_000, 0, 100_000, 0, 0)
        equity.add_many("LIVE-1", [
            (NOW + timedelta(days=1), 99_000, 1_000, 100_000, 0, 0),
            (NOW + timedelta(days=2), 98_000, 2_000, 100_000, 0, 0.01),
        ])
        points = equity.list_by_session("LIVE-1")
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0]["equity"], 100_000)
        self.assertEqual(equity.latest("LIVE-1")["equity"], 100_000)
        self.assertEqual(equity.count("LIVE-1"), 3)
        self.assertIsNone(equity.latest("missing"))

    # ------------------------------------------------------------ 回测结果

    def test_backtest_roundtrip(self) -> None:
        bars = make_bars(days=150)
        config = BacktestConfig(symbols=(SYMBOL,), start=bars[0].dt.date(),
                                end=bars[-1].dt.date(), strategy_key="ma_cross",
                                fee=FeeProfile(slippage_bps=0.0),
                                max_volume_ratio=1.0)
        engine = BacktestEngine(config, {SYMBOL: bars},
                                StrategyRegistry().create("ma_cross"),
                                InstrumentRegistry().with_defaults(), config.fee)
        result = engine.run()
        run_id = self.repos.backtests.save(result)
        self.assertEqual(run_id, result.run_id)
        runs = self.repos.backtests.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["strategy_key"], "ma_cross")
        loaded = self.repos.backtests.load(run_id)
        self.assertEqual(len(loaded.equity_curve), len(result.equity_curve))
        self.assertEqual(len(loaded.trades), len(result.trades))
        self.assertEqual(loaded.metrics["total_return"],
                         result.metrics["total_return"])
        self.assertEqual(self.repos.backtests.count(), 1)
        self.assertTrue(self.repos.backtests.delete(run_id))
        self.assertIsNone(self.repos.backtests.load(run_id))

    # ------------------------------------------------------------ 事务

    def test_transaction_rolls_back(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.execute(
                    "INSERT INTO kv(key, value, updated_at) VALUES (?,?,?)",
                    ("tx", "1", NOW.isoformat()))
                raise RuntimeError("boom")
        self.assertIsNone(self.repos.kv.get("tx"))


if __name__ == "__main__":
    unittest.main()
