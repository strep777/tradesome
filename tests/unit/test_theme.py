"""主题的高 DPI 缩放测试（不创建窗口）。"""

from __future__ import annotations

import unittest

from tradesome.ui import theme


class ThemeDpiTest(unittest.TestCase):
    def tearDown(self) -> None:
        theme.set_dpi(1.0)
        theme.set_theme("dark", 1.0)

    def test_px_scales_with_dpi(self) -> None:
        theme.set_dpi(1.25)
        theme.set_theme("dark", 1.0)
        self.assertEqual(theme.px(100), 125)
        self.assertEqual(theme.px(4), 5)

    def test_px_scales_with_user_scale(self) -> None:
        theme.set_dpi(1.0)
        theme.set_theme("dark", 1.2)
        self.assertEqual(theme.px(100), 120)

    def test_px_combines_dpi_and_user_scale(self) -> None:
        theme.set_dpi(1.25)
        theme.set_theme("dark", 1.2)
        self.assertEqual(theme.px(100), 150)

    def test_dpi_is_clamped(self) -> None:
        theme.set_dpi(9.0)
        self.assertAlmostEqual(theme.dpi(), 3.0)
        theme.set_dpi(0.1)
        self.assertAlmostEqual(theme.dpi(), 0.75)
        theme.set_dpi("bad")  # type: ignore[arg-type]
        self.assertAlmostEqual(theme.dpi(), 1.0)

    def test_fonts_do_not_multiply_dpi(self) -> None:
        theme.set_dpi(2.0)
        theme.set_theme("dark", 1.0)
        family, size, weight = theme.font("body")
        self.assertEqual(size, 10)
        self.assertEqual(weight, "normal")

    def test_dpi_awareness_entry_point_is_safe(self) -> None:
        from tradesome.ui import enable_dpi_awareness

        # 重复调用必须无副作用；返回值只表示"本次是否成功声明"。
        self.assertIsInstance(enable_dpi_awareness(), bool)
        self.assertIsInstance(enable_dpi_awareness(), bool)


if __name__ == "__main__":
    unittest.main()
