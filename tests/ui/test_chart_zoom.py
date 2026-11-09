"""K 线缩放与坐标轴跟随的回归测试。"""

import time
import tkinter as tk
import unittest

from tests.support import make_bars
from tradesome.ui import theme
from tradesome.ui.charts import CandlestickChart

try:
    _root = tk.Tk()
    _root.destroy()
    HAS_DISPLAY = True
except Exception:  # pragma: no cover - 无显示环境
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面")
class CandlestickZoomTest(unittest.TestCase):
    def setUp(self) -> None:
        theme.set_theme("macos", 1.0)
        self.root = tk.Tk()
        self.root.geometry("900x420")
        self.chart = CandlestickChart(self.root, height=320)
        self.chart.pack(fill="both", expand=True)
        self.bars = make_bars(days=300)
        self.chart.set_bars(self.bars)
        self.root.update()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_initial_view_is_full_range(self) -> None:
        self.assertEqual(self.chart.view_range(), (0, 300))

    def test_zoom_in_keeps_anchor_and_axes_redraw(self) -> None:
        self.chart.zoom_at(0.5, 0.5)
        start, end = self.chart.view_range()
        self.assertLess(end - start, 300)
        self.assertGreater(start, 0)
        time.sleep(0.12)
        self.root.update()
        self.assertGreater(len(self.chart.find_all()), 50)

    def test_zoom_at_left_edge_stays_at_start(self) -> None:
        self.chart.zoom_at(0.0, 0.4)
        start, end = self.chart.view_range()
        self.assertEqual(start, 0)
        self.assertLess(end, 300)

    def test_zoom_out_never_exceeds_data(self) -> None:
        self.chart.zoom_at(0.5, 0.2)
        self.chart.zoom_at(0.5, 10.0)
        self.assertEqual(self.chart.view_range(), (0, 300))

    def test_minimum_window_is_enforced(self) -> None:
        for _ in range(20):
            self.chart.zoom_at(0.5, 0.5)
        start, end = self.chart.view_range()
        self.assertGreaterEqual(end - start, CandlestickChart.MIN_BARS)

    def test_pan_moves_window_and_clamps(self) -> None:
        self.chart.zoom_at(0.5, 0.4)
        start, end = self.chart.view_range()
        self.chart.pan(10)
        self.assertEqual(self.chart.view_range(), (start + 10, end + 10))
        self.chart.pan(-10_000)
        self.assertEqual(self.chart.view_range()[0], 0)
        self.chart.pan(10_000)
        self.assertEqual(self.chart.view_range()[1], 300)

    def test_wheel_event_zooms_around_pointer(self) -> None:
        self.chart.event_generate("<MouseWheel>", delta=120, x=120, y=120)
        self.root.update()
        start, end = self.chart.view_range()
        self.assertLess(end - start, 300)
        self.assertGreaterEqual(start, 0)

    def test_drag_pans_visible_window(self) -> None:
        self.chart.zoom_at(0.5, 0.4)
        start, end = self.chart.view_range()
        self.chart.event_generate("<ButtonPress-1>", x=600, y=200)
        self.chart.event_generate("<B1-Motion>", x=500, y=200)
        self.chart.event_generate("<ButtonRelease-1>", x=500, y=200)
        self.root.update()
        moved = self.chart.view_range()
        self.assertNotEqual(moved, (start, end))

    def test_reset_restores_full_range(self) -> None:
        self.chart.zoom_at(0.7, 0.3)
        self.chart.reset_zoom()
        self.assertEqual(self.chart.view_range(), (0, 300))
