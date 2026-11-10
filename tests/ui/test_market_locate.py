"""行情页：按日期定位与视图区间联动。"""

from __future__ import annotations

import time
import unittest

from tradesome.ui import theme
from tradesome.ui.shell import Shell

from tests.support import TempWorkspace, build_memory_container
from tests.logging_utils import close_log_handlers
from tests.ui.test_ui_smoke import HAS_DISPLAY


@unittest.skipUnless(HAS_DISPLAY, "需要图形环境")
class MarketLocateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = TempWorkspace()
        self.container = build_memory_container(self.ws.root, with_demo=True)
        self.app = Shell(self.container, welcome=False)
        self.errors = []
        self.app.show_error = lambda title, message, hint="": self.errors.append(
            (title, message))
        self.app.update()
        for _ in range(6):
            self.app.update()
            time.sleep(0.04)
        self.app.navigate("market")
        for _ in range(6):
            self.app.update()
            time.sleep(0.04)
        self.page = self.app._pages["market"]

    def tearDown(self) -> None:
        self.app.destroy()
        from tradesome.app.bootstrap import shutdown
        shutdown(self.container)
        close_log_handlers()
        self.ws.cleanup()

    def test_locate_centers_and_updates_range_label(self) -> None:
        self.page.locate_entry.delete(0, "end")
        self.page.locate_entry.insert(0, "2023-06-05")
        self.page._locate_date()
        self.app.update()
        start, end = self.page.chart.view_range()
        self.assertEqual(end - start, 120)
        visible = self.page._bars[start:end]
        dates = [bar.dt.date().isoformat() for bar in visible]
        self.assertIn("2023-06-05", dates)
        self.assertEqual(self.page.stats["range"].value_label.cget("text"),
                         "%s ~ %s" % (dates[0], dates[-1]))
        self.assertEqual(self.errors, [])

    def test_locate_switches_to_full_history(self) -> None:
        self.page.range_var.set("近 1 年")
        self.page._load_chart()
        self.page.locate_entry.delete(0, "end")
        self.page.locate_entry.insert(0, "2022-06-01")
        self.page._locate_date()
        self.assertEqual(self.page.range_var.get(), "全部历史")
        self.assertGreater(len(self.page._bars), 900)

    def test_invalid_date_is_rejected_without_crash(self) -> None:
        self.page.locate_entry.delete(0, "end")
        self.page.locate_entry.insert(0, "2023/06/05")
        self.page._locate_date()
        self.assertEqual(self.errors, [])

    def test_sync_modal_defaults_to_full_history(self) -> None:
        from tradesome.ui.widgets.akshare_sync import AkshareSyncModal

        modal = AkshareSyncModal(self.page, self.container.services.market)
        try:
            self.assertEqual(modal.start_entry.get(), "1990-01-01")
        finally:
            modal.destroy()


if __name__ == "__main__":
    unittest.main()
