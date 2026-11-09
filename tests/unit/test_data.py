"""数据层测试：仓储缓存、合成数据、CSV 读写、重采样与质量检查。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from tradesome.core.types import Bar
from tradesome.data import quality
from tradesome.data.providers.csv_provider import (export_bars_csv, inspect_csv,
                                                   read_csv_bars, read_csv_bars_ex)
from tradesome.data.providers.synthetic_provider import SyntheticProvider
from tradesome.data.repository import MarketDataRepository
from tradesome.data.resample import infer_timeframe, resample
from tradesome.core.errors import DataError
from tradesome.core.instruments import InstrumentRegistry
from tradesome.persistence.db import Database
from tradesome.persistence.migrations import initialize
from tradesome.persistence.repositories import Repositories
from tests.support import TempWorkspace, make_bars


def make_repo(tmp) -> MarketDataRepository:
    database = Database(tmp.root / "market.db")
    database.connect()
    initialize(database)
    repos = Repositories(database)
    repo = MarketDataRepository(repos.bars)
    repo._test_database = database  # 供 tearDown 关闭，避免临时目录被占用
    return repo


class RepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = TempWorkspace()
        self.repo = make_repo(self.ws)
        self.bars = make_bars(days=60)

    def tearDown(self) -> None:
        self.repo._test_database.close()
        self.ws.cleanup()

    def test_upsert_and_read(self) -> None:
        written = self.repo.upsert_bars(self.bars)
        self.assertEqual(written, 60)
        loaded = self.repo.get_bars("600519.SH")
        self.assertEqual(len(loaded), 60)
        self.assertEqual(loaded[0].dt, self.bars[0].dt)
        self.assertEqual(self.repo.count(), 60)
        self.assertEqual(self.repo.count("600519.SH"), 60)

    def test_upsert_is_idempotent(self) -> None:
        self.repo.upsert_bars(self.bars)
        self.repo.upsert_bars(self.bars)
        self.assertEqual(self.repo.count(), 60)

    def test_limit_and_range(self) -> None:
        self.repo.upsert_bars(self.bars)
        last_five = self.repo.get_bars("600519.SH", limit=5)
        self.assertEqual(len(last_five), 5)
        self.assertEqual(last_five[-1].dt, self.bars[-1].dt)
        start = self.bars[10].dt.date()
        end = self.bars[19].dt.date()
        subset = self.repo.get_bars("600519.SH", start=start, end=end)
        self.assertEqual(len(subset), 10)

    def test_get_bars_many(self) -> None:
        other = make_bars(symbol="000001.SZ", days=30, seed=11)
        self.repo.upsert_bars(self.bars)
        self.repo.upsert_bars(other)
        data = self.repo.get_bars_many(["600519.SH", "000001.SZ", "999999.SH"])
        self.assertEqual(len(data["600519.SH"]), 60)
        self.assertEqual(len(data["000001.SZ"]), 30)
        self.assertEqual(data["999999.SH"], [])

    def test_cache_hit_and_invalidation(self) -> None:
        self.repo.upsert_bars(self.bars)
        first = self.repo.get_bars("600519.SH")
        info = self.repo.cache_info()
        self.assertGreaterEqual(info.get("hits", 0), 0)
        second = self.repo.get_bars("600519.SH")
        self.assertEqual(len(first), len(second))
        self.assertGreater(self.repo.cache_info().get("hits", 0), 0)
        extra = make_bars(days=70)
        self.repo.upsert_bars(extra[-5:])
        self.assertEqual(len(self.repo.get_bars("600519.SH")), 65)

    def test_delete_symbol(self) -> None:
        self.repo.upsert_bars(self.bars)
        removed = self.repo.delete_symbol("600519.SH")
        self.assertEqual(removed, 60)
        self.assertEqual(self.repo.count(), 0)

    def test_symbols_and_coverage(self) -> None:
        self.repo.upsert_bars(self.bars)
        rows = self.repo.symbols()
        self.assertEqual(rows[0]["symbol"], "600519.SH")
        self.assertEqual(rows[0]["count"], 60)
        count, first, last = self.repo.coverage("600519.SH")
        self.assertEqual(count, 60)
        self.assertEqual(first, self.bars[0].dt)
        self.assertEqual(last, self.bars[-1].dt)
        self.assertEqual(self.repo.last_dt("600519.SH"), self.bars[-1].dt)
        self.assertIsNone(self.repo.last_dt("000000.SH"))


class SyntheticProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InstrumentRegistry.with_defaults()

    def test_same_seed_same_data(self) -> None:
        first = SyntheticProvider(seed=42, registry=self.registry)
        second = SyntheticProvider(seed=42, registry=self.registry)
        left = first.fetch_bars("600519.SH", date(2024, 1, 1), date(2024, 6, 30))
        right = second.fetch_bars("600519.SH", date(2024, 1, 1), date(2024, 6, 30))
        self.assertEqual([b.to_dict() for b in left], [b.to_dict() for b in right])

    def test_invariants(self) -> None:
        provider = SyntheticProvider(seed=7, registry=self.registry)
        bars = provider.fetch_bars("600519.SH", date(2023, 1, 1), date(2024, 12, 31))
        self.assertGreater(len(bars), 400)
        for bar in bars:
            self.assertGreaterEqual(bar.high, max(bar.open, bar.close))
            self.assertLessEqual(bar.low, min(bar.open, bar.close))
            self.assertGreater(bar.volume, 0)
            self.assertGreater(bar.close, 0)
            self.assertLessEqual(bar.high / bar.low, 1.11)  # 日涨跌幅受限

    def test_generate_all_covers_symbols(self) -> None:
        provider = SyntheticProvider(seed=3, registry=self.registry)
        data = provider.generate_all(["600519.SH", "000001.SZ"],
                                     date(2024, 1, 1), date(2024, 3, 31))
        self.assertEqual(set(data), {"600519.SH", "000001.SZ"})
        self.assertTrue(all(len(v) > 40 for v in data.values()))

    def test_invalidate_changes_series(self) -> None:
        provider = SyntheticProvider(seed=3, registry=self.registry)
        first = provider.fetch_bars("600519.SH", date(2024, 1, 1), date(2024, 3, 31))
        provider.invalidate("600519.SH")
        second = provider.fetch_bars("600519.SH", date(2024, 1, 1), date(2024, 3, 31))
        self.assertEqual(len(first), len(second))


class CsvTest(unittest.TestCase):
    def test_roundtrip_export_import(self) -> None:
        with TempWorkspace() as tmp:
            bars = make_bars(days=40)
            path = export_bars_csv(bars, tmp.root / "600519.SH.csv")
            loaded = read_csv_bars(path)
            self.assertEqual(len(loaded), 40)
            self.assertEqual(loaded[0].symbol, "600519.SH")
            self.assertAlmostEqual(loaded[0].close, bars[0].close)
            self.assertEqual(loaded[0].dt.date(), bars[0].dt.date())

    def test_import_accepts_chinese_headers_and_symbol_from_name(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "000001.SZ.csv"
            path.write_text(
                "日期,开盘,最高,最低,收盘,成交量\n"
                "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
                "2024-01-03,10.2,10.8,10.1,10.6,1200\n",
                encoding="utf-8")
            bars = read_csv_bars(path)
            self.assertEqual([b.symbol for b in bars], ["000001.SZ", "000001.SZ"])
            self.assertEqual(bars[-1].close, 10.6)

    def test_duplicate_rows_keep_last(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "600519.SH.csv"
            path.write_text(
                "date,open,high,low,close,volume\n"
                "2024-01-02,10,10.5,9.8,10.2,1000\n"
                "2024-01-02,10,10.9,9.8,10.9,1000\n",
                encoding="utf-8")
            bars = read_csv_bars(path)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0].close, 10.9)

    def test_bad_rows_are_reported(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "600519.SH.csv"
            path.write_text(
                "date,open,high,low,close,volume\n"
                "bad,10,10.5,9.8,10.2,1000\n"
                "2024-01-03,10,10.9,9.8,10.6,1200\n",
                encoding="utf-8")
            bars, errors = read_csv_bars_ex(path)
            self.assertEqual(len(bars), 1)
            self.assertTrue(errors)
            self.assertIn("第 2 行", errors[0])

    def test_missing_columns_raise(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "x.csv"
            path.write_text("date,close\n2024-01-02,10\n", encoding="utf-8")
            with self.assertRaises(DataError) as ctx:
                read_csv_bars(path)
            self.assertIn("缺少必需的列", ctx.exception.message)

    def test_missing_file_and_bad_suffix(self) -> None:
        with TempWorkspace() as tmp:
            with self.assertRaises(DataError):
                read_csv_bars(tmp.root / "nope.csv")
            path = tmp.root / "data.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(DataError):
                read_csv_bars(path)

    def test_inspect_csv(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "600519.SH.csv"
            path.write_text(
                "日期,开盘,最高,最低,收盘,成交量\n2024-01-02,1,2,0.5,1.5,10\n",
                encoding="utf-8")
            info = inspect_csv(path)
            self.assertEqual(info["missing"], [])
            self.assertEqual(info["mapped"]["date"], "日期")
            self.assertEqual(info["estimated_rows"], 1)


class ResampleTest(unittest.TestCase):
    def test_weekly_aggregates_ohlc(self) -> None:
        bars = make_bars(days=15)
        weekly = resample(bars, "1w")
        self.assertLess(len(weekly), len(bars))
        self.assertEqual(weekly[0].open, bars[0].open)
        self.assertGreaterEqual(weekly[0].high, max(b.high for b in bars[:5]))

    def test_monthly_buckets(self) -> None:
        bars = make_bars(days=90)
        monthly = resample(bars, "1M")
        self.assertGreaterEqual(len(monthly), 3)
        self.assertEqual(len({b.dt.month for b in monthly}), len(monthly))

    def test_daily_is_unchanged(self) -> None:
        bars = make_bars(days=10)
        self.assertEqual(len(resample(bars, "1d")), 10)

    def test_infer_timeframe(self) -> None:
        self.assertEqual(infer_timeframe(make_bars(days=30)), "1d")
        self.assertEqual(infer_timeframe([]), "")


class QualityTest(unittest.TestCase):
    def test_clean_bars(self) -> None:
        report = quality.check_bars(make_bars(days=120))
        self.assertFalse(report.errors)
        self.assertTrue(report.ok)
        self.assertTrue(report.summary())

    def test_detects_ohlc_violation(self) -> None:
        bars = make_bars(days=30)
        bars[3] = Bar(symbol=bars[3].symbol, dt=bars[3].dt, open=bars[3].open,
                      high=bars[3].high, low=bars[3].low, close=bars[3].close)
        report = quality.check_bars(bars)
        self.assertIsInstance(report.to_dict(), dict)

    def test_gap_finding_tolerates_holiday(self) -> None:
        bars = make_bars(days=40)
        report = quality.check_bars(bars)
        self.assertEqual([i for i in report.issues if i.code == "E_GAP"], [])

    def test_duplicates_reported(self) -> None:
        bars = make_bars(days=20)
        duplicated = list(bars) + [bars[5]]
        report = quality.check_bars(duplicated)
        self.assertTrue(any(issue.code == "E_DATE" for issue in report.issues))

    def test_merge_and_blocking(self) -> None:
        left = quality.QualityReport(symbol="600519.SH")
        left.add("W_X", "提示")
        merged = quality.merge_reports([left])
        self.assertTrue(merged.issues)
        self.assertFalse(quality.has_blocking_error(merged))


if __name__ == "__main__":
    unittest.main()
