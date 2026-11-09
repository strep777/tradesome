"""akshare 清洗层与同步服务测试（不联网、不需要安装 akshare）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from tradesome.app.bootstrap import build_container, shutdown
from tradesome.data.providers.akshare_csv import (records_to_bars, write_bars,
                                                  write_records)
from tradesome.data.providers.csv_provider import read_csv_bars


def _records():
    return [
        {"日期": "2024-01-02", "开盘": "10.0", "最高": "10.5", "最低": "9.8",
         "收盘": "10.2", "成交量": "12,345", "成交额": "125000"},
        {"日期": "20240103", "开盘": 10.2, "最高": 10.8, "最低": 10.0,
         "收盘": 10.6, "成交量": 20000, "成交额": 210000},
        {"日期": "2024-01-03", "开盘": 10.2, "最高": 10.8, "最低": 10.0,
         "收盘": 10.7, "成交量": 20000, "成交额": 210000},
        {"日期": "坏日期", "开盘": 1, "最高": 1, "最低": 1, "收盘": 1},
        {"date": "2024-01-04", "open": 10.7, "high": 11.0, "low": 10.6,
         "close": 10.9, "volume": 30000},
    ]


class RecordsTest(unittest.TestCase):
    def test_chinese_headers_and_dedup(self):
        bars, skipped = records_to_bars(_records(), "600519.SH")
        self.assertEqual(len(bars), 3)
        self.assertEqual(skipped, 1)
        self.assertEqual(bars[0].dt, datetime(2024, 1, 2))
        self.assertEqual(bars[0].volume, 12345.0)
        # 同一天后出现的记录覆盖前一条
        self.assertAlmostEqual(bars[1].close, 10.7)
        self.assertEqual(bars[-1].close, 10.9)

    def test_bad_ohlc_is_skipped(self):
        rows = [{"日期": "2024-01-02", "开盘": 1, "最高": 0.5, "最低": 0.4,
                 "收盘": 0.8, "成交量": 1}]
        bars, skipped = records_to_bars(rows, "600519.SH")
        self.assertEqual(bars, [])
        self.assertEqual(skipped, 1)

    def test_non_dict_rows(self):
        bars, skipped = records_to_bars(["bad", None], "600519.SH")
        self.assertEqual((bars, skipped), ([], 2))


class WriteTest(unittest.TestCase):
    def test_roundtrip_with_csv_provider(self):
        bars, _ = records_to_bars(_records(), "600519.SH")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "600519.SH.csv"
            count = write_bars(bars, path)
            self.assertEqual(count, 3)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            loaded = read_csv_bars(path, "600519.SH")
            self.assertEqual(len(loaded), 3)
            self.assertAlmostEqual(loaded[-1].close, 10.9)
            self.assertEqual(loaded[-1].dt.date(), date(2024, 1, 4))

    def test_write_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.csv"
            count, skipped = write_records(_records(), "600519.SH", path)
            self.assertEqual((count, skipped), (3, 1))


class SyncServiceTest(unittest.TestCase):
    def setUp(self):
        self.c = build_container(memory_db=True, with_demo=False)

    def tearDown(self):
        shutdown(self.c)

    def test_status_unavailable(self):
        fake = mock.Mock(returncode=2, stdout="", stderr="缺少 akshare")
        with mock.patch("tradesome.app.services.market_service.subprocess.run",
                        return_value=fake):
            status = self.c.services.market.akshare_status()
        self.assertFalse(status["available"])
        self.assertIn("akshare", status["message"])

    def test_sync_imports_files(self):
        bars, _ = records_to_bars(_records(), "600519.SH")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "600519.SH.csv"
            write_bars(bars, path)
            payload = ('{"ok": true, "bars": 3, "files": [{"symbol": "600519.SH", '
                       '"path": "%s", "bars": 3}], "errors": []}' % path.as_posix())
            fake = mock.Mock(returncode=0, stdout=payload, stderr="")
            with mock.patch("tradesome.app.services.market_service.subprocess.run",
                            return_value=fake):
                result = self.c.services.market.sync_akshare(["600519.SH"])
        self.assertEqual(result["symbols"], 1)
        self.assertEqual(result["bars"], 3)
        self.assertEqual(self.c.repos.bars.count(), 3)

    def test_purge_all(self):
        self.c.services.market.generate_demo()
        self.assertGreater(self.c.repos.bars.count(), 0)
        result = self.c.services.market.purge_all()
        self.assertGreater(result["bars"], 0)
        self.assertEqual(self.c.repos.bars.count(), 0)


class InstallServiceTest(unittest.TestCase):
    def setUp(self):
        self.c = build_container(memory_db=True, with_demo=False)
        self.market = self.c.services.market

    def tearDown(self):
        shutdown(self.c)

    def test_install_builds_fixed_command(self):
        fake = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("tradesome.app.services.market_service.subprocess.run",
                        return_value=fake) as run:
            result = self.market.install_akshare()
        self.assertTrue(result["ok"])
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("akshare==1.18.88", cmd)
        self.assertNotIn("--no-index", cmd)

    def test_install_uses_compatible_vendor_wheels(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "akshare-1.14.0-py3-none-any.whl").write_bytes(b"x")
            fake = mock.Mock(returncode=0, stdout="ok", stderr="")
            prop = mock.PropertyMock(return_value=Path(folder))
            with mock.patch.object(type(self.market), "_vendor_dir", prop), \
                    mock.patch("tradesome.app.services.market_service.subprocess.run",
                               return_value=fake) as run:
                result = self.market.install_akshare()
        self.assertTrue(result["ok"])
        self.assertEqual(result["wheels"], 1)
        cmd = run.call_args[0][0]
        self.assertIn("--find-links", cmd)
        # 不能加 --no-index：本地 wheel 缺依赖时还要能联网补齐
        self.assertNotIn("--no-index", cmd)

    def test_install_skips_incompatible_wheel(self):
        with tempfile.TemporaryDirectory() as folder:
            wheel = Path(folder) / "akshare-1.20.36-py3-none-any.whl"
            with zipfile.ZipFile(str(wheel), "w") as archive:
                archive.writestr(
                    "akshare-1.20.36.dist-info/METADATA",
                    "Metadata-Version: 2.1\nRequires-Python: >=3.11\n")
            fake = mock.Mock(returncode=1, stdout="",
                             stderr="ERROR: Requires-Python >=3.11")
            prop = mock.PropertyMock(return_value=Path(folder))
            with mock.patch.object(type(self.market), "_vendor_dir", prop), \
                    mock.patch("tradesome.app.services.market_service.subprocess.run",
                               return_value=fake) as run:
                result = self.market.install_akshare()
        self.assertFalse(result["ok"])
        self.assertEqual(result["wheels"], 0)
        self.assertEqual(result["skipped_wheels"], 1)
        self.assertIn("不兼容", result["message"])
        cmd = run.call_args[0][0]
        self.assertNotIn("--find-links", cmd)
        self.assertIn("Requires-Python", result["message"])

    def test_install_timeout_message(self):
        with mock.patch("tradesome.app.services.market_service.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("pip", 10)):
            result = self.market.install_akshare(timeout=10)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["message"])

    def test_ensure_fails_when_still_not_importable(self):
        with mock.patch.object(type(self.market), "akshare_installed",
                               return_value=False), \
                mock.patch.object(type(self.market), "install_akshare",
                                  return_value={"ok": True, "message": "done"}):
            result = self.market.ensure_akshare()
        self.assertFalse(result["ok"])
        self.assertIn("仍无法导入", result["message"])

    def test_ensure_short_circuits_when_installed(self):
        with mock.patch.object(type(self.market), "akshare_installed",
                               return_value=True):
            result = self.market.ensure_akshare()
        self.assertTrue(result["ok"])
        self.assertFalse(result["installed"])


class DefaultConfigTest(unittest.TestCase):
    def test_no_demo_by_default(self):
        from tradesome.core.config import AppConfig

        self.assertFalse(AppConfig().data.auto_generate_demo)

    def test_auto_install_akshare_by_default(self):
        from tradesome.core.config import AppConfig

        self.assertTrue(AppConfig().data.auto_install_akshare)


if __name__ == "__main__":
    unittest.main()
