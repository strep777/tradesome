"""应用装配、路径与命令行入口测试。"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from tradesome.app import Paths, build_container, shutdown
from tradesome.app.paths import Paths as PathsClass
from tests.logging_utils import close_log_handlers
from tests.support import TempWorkspace


class PathsTest(unittest.TestCase):
    def test_ensure_creates_directories(self) -> None:
        with TempWorkspace() as ws:
            paths = Paths(ws.root)
            paths.ensure()
            for folder in (paths.root, paths.data_dir, paths.exports,
                           paths.logs, paths.strategies, paths.backups):
                self.assertTrue(Path(folder).is_dir())
            self.assertEqual(Path(paths.db).name, "tradesome.db")

    def test_default_uses_project_root(self) -> None:
        paths = PathsClass.default()
        self.assertTrue(str(paths.root))


class BootstrapTest(unittest.TestCase):
    def test_build_and_shutdown(self) -> None:
        with TempWorkspace() as ws:
            container = build_container(root=ws.root, memory_db=True,
                                        with_demo=False)
            try:
                self.assertIsNotNone(container.services)
                self.assertIsNotNone(container.repos)
                self.assertIsNotNone(container.bus)
                self.assertGreaterEqual(len(container.strategies.keys()), 6)
                self.assertEqual(container.services.market.summary()["bars"], 0)
            finally:
                shutdown(container)
                close_log_handlers()

    def test_demo_generation_on_boot(self) -> None:
        with TempWorkspace() as ws:
            container = build_container(root=ws.root, memory_db=True,
                                        with_demo=True)
            try:
                self.assertEqual(container.services.market.summary()["symbols"], 8)
            finally:
                shutdown(container)
                close_log_handlers()

    def test_audit_records_start_and_stop(self) -> None:
        with TempWorkspace() as ws:
            container = build_container(root=ws.root, memory_db=False,
                                        with_demo=False)
            rows = container.repos.audit.list_recent(20, category="app")
            self.assertTrue(any(r["action"] == "start" for r in rows))
            shutdown(container)
            conn = sqlite3.connect(str(container.paths.db))
            try:
                actions = [row[0] for row in conn.execute(
                    "SELECT action FROM audit_log WHERE category='app'")]
            finally:
                conn.close()
            self.assertIn("stop", actions)
            close_log_handlers()


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "tradesome", *args],
                              cwd=str(Path(__file__).resolve().parents[2]),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)

    def test_version(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("TradeSome", result.stdout)

    def test_help_lists_commands(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for word in ("demo-data", "backtest", "selftest"):
            self.assertIn(word, result.stdout)

    def test_gui_entry_is_importable(self) -> None:
        """CLI 通过 tradesome.ui.app 启动图形界面，入口不能缺失。"""
        from tradesome.ui.app import run

        self.assertTrue(callable(run))


if __name__ == "__main__":
    unittest.main()
