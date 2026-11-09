"""界面冒烟测试：真实创建窗口，逐页切换，确认没有异常弹窗。"""

from __future__ import annotations

import time
import unittest

from tradesome.app.bootstrap import build_container, shutdown
from tradesome.ui import theme
from tests.logging_utils import close_log_handlers
from tests.support import TempWorkspace

try:  # 无桌面环境时跳过，而不是让整套测试失败
    import tkinter as tk

    _root = tk.Tk()
    _root.destroy()
    HAS_DISPLAY = True
except Exception:  # noqa: BLE001
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "当前环境没有可用的图形界面")
class ShellSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        from tradesome.ui.shell import PAGE_SPECS, Shell

        self.ws = TempWorkspace()
        self.container = build_container(root=self.ws.root, memory_db=True,
                                         with_demo=True)
        self.errors = []
        self.app = Shell(self.container, welcome=False)
        self.app.show_error = lambda *args, **kwargs: self.errors.append(args)
        self.keys = [spec[0] for spec in PAGE_SPECS]
        self.app.update()
        time.sleep(0.35)
        self.app.update()

    def tearDown(self) -> None:
        try:
            self.app.destroy()
        except Exception:  # noqa: BLE001
            pass
        shutdown(self.container)
        close_log_handlers()
        self.ws.cleanup()

    def test_all_pages_render(self) -> None:
        for key in self.keys:
            self.app.navigate(key)
            self.app.update()
            page = self.app._pages.get(key)
            self.assertIsNotNone(page, "页面 %s 没有创建" % key)
            self.assertEqual(self.app._current, key)
            self.assertTrue(page.winfo_ismapped())
        self.assertEqual(self.errors, [])

    def test_theme_toggle(self) -> None:
        before = theme.theme_name()
        seen = {before}
        for _ in range(3):
            self.app.toggle_theme()
            self.app.update()
            seen.add(theme.theme_name())
        # macOS → 深色 → 浅色 → macOS：三档循环，且三档都出现过。
        self.assertEqual(theme.theme_name(), before)
        self.assertEqual(len(seen), 3)

    def test_navigation_keeps_single_page(self) -> None:
        self.app.navigate("market")
        self.app.update()
        self.app.navigate("backtest")
        self.app.update()
        mapped = [key for key, page in self.app._pages.items()
                  if page.winfo_ismapped()]
        self.assertEqual(mapped, ["backtest"])

    def test_toast_leaves_no_ghost_when_empty(self) -> None:
        stack = self.app.toasts._stack
        self.app.toasts.show("测试提示", "info", duration=10)
        self.app.update()
        self.assertTrue(stack.winfo_ismapped(), "提示应显示")
        for _ in range(30):
            self.app.update()
            time.sleep(0.02)
        self.assertFalse(stack.winfo_ismapped(), "提示消失后不应留下空色块")

    def test_strategy_wizard_saves_profile(self) -> None:
        from tradesome.ui.widgets.strategy_wizard import StrategyWizard

        self.app.navigate("strategies")
        self.app.update()
        page = self.app._pages["strategies"]
        page._new()
        self.app.update()
        wizards = [w for w in self.app.winfo_children()
                   if isinstance(w, StrategyWizard)]
        self.assertTrue(wizards, "向导没有打开")
        wizard = wizards[-1]
        wizard.name_entry.set("冒烟新建策略")
        wizard._save()
        self.app.update()
        names = [row["name"] for row in
                 self.container.services.strategy.metas()]
        self.assertIn("冒烟新建策略", names)

    def test_sidebar_keeps_single_active_item(self) -> None:
        for key in ("market", "strategies", "lab", "backtest", "live"):
            self.app.navigate(key)
            self.app.update()
            self.assertEqual(self.app.sidebar.active_key(), key)
            active = [item for item, row in self.app.sidebar._rows.items()
                      if row["canvas"].find_withtag("all")]
            self.assertIn(key, active)

    def test_tour_highlights_target_and_closes(self) -> None:
        for _ in range(20):
            self.app.update()
            time.sleep(0.02)
        self.app.start_guide(0)
        for _ in range(20):
            self.app.update()
            time.sleep(0.02)
        self.assertIsNotNone(self.app.tour._card)
        self.assertTrue(self.app.tour._card.winfo_ismapped())
        self.assertEqual(self.app._current, "dashboard")
        self.assertTrue(self.app.guide_done(), "开始引导即视为已看过欢迎框")

        self.app.tour._next()
        for _ in range(10):
            self.app.update()
            time.sleep(0.02)
        self.assertEqual(self.app._current, "data")

        self.app.tour.close()
        self.app.update()
        self.assertIsNone(self.app.tour._card)

    def test_page_help_steps_open(self) -> None:
        from tradesome.ui.widgets.tour import StepsModal

        self.app.navigate("backtest")
        self.app.update()
        page = self.app._pages["backtest"]
        self.assertTrue(page.help_steps)
        self.app.show_help_steps(page)
        self.app.update()
        modals = [w for w in self.app.winfo_children()
                  if isinstance(w, StepsModal)]
        self.assertTrue(modals, "「本页怎么用」没有打开")
        modals[-1].close()
        self.app.update()

    def test_strategy_wizard_rules_mode(self) -> None:
        from tradesome.ui.widgets.strategy_wizard import StrategyWizard

        self.app.navigate("strategies")
        self.app.update()
        page = self.app._pages["strategies"]
        page._new()
        self.app.update()
        wizards = [w for w in self.app.winfo_children()
                   if isinstance(w, StrategyWizard)]
        wizard = wizards[-1]
        wizard._set_mode("rules")
        self.app.update()
        wizard.name_entry.set("从零组合冒烟")
        wizard._save()
        self.app.update()
        names = [row["name"] for row in
                 self.container.services.strategy.metas()]
        self.assertIn("从零组合冒烟", names)
        key = [row["key"] for row in self.container.services.strategy.metas()
               if row["name"] == "从零组合冒烟"][0]
        meta = self.container.services.strategy.meta(key)
        self.assertEqual(meta["kind"], "rules")


if __name__ == "__main__":
    unittest.main()
