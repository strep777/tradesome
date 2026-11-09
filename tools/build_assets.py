"""生成应用图标与示例数据（一次性构建脚本，可重复运行）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

ACCENT = (76, 141, 255)
UP = (240, 68, 82)
DOWN = (34, 197, 94)
BG = (15, 17, 21)
SURFACE = (23, 26, 33)


def draw_icon(size: int) -> Image.Image:
    scale = size / 256.0
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(56 * scale)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG,
                           outline=(42, 48, 59, 255), width=max(1, int(2 * scale)))
    pad = 44 * scale
    # 三根 K 线：两根阳线、一根阴线
    candles = [
        (0.20, 0.55, 0.30, 0.78, UP),
        (0.45, 0.32, 0.26, 0.62, DOWN),
        (0.70, 0.16, 0.24, 0.50, UP),
    ]
    body_w = 26 * scale
    for cx, top, height, wick, color in candles:
        x = pad + (size - 2 * pad) * cx
        y_top = pad + (size - 2 * pad) * top
        y_bottom = y_top + (size - 2 * pad) * height
        y_high = y_top - (size - 2 * pad) * wick * 0.35
        y_low = y_bottom + (size - 2 * pad) * wick * 0.35
        draw.line([(x, y_high), (x, y_low)], fill=color, width=max(2, int(4 * scale)))
        draw.rounded_rectangle([x - body_w / 2, y_top, x + body_w / 2, y_bottom],
                               radius=int(4 * scale), fill=color)
    # 底部一条走势线
    points = []
    for i in range(9):
        ratio = i / 8.0
        x = pad * 0.9 + (size - 1.8 * pad) * ratio
        y = size - pad - (size - 2 * pad) * 0.08 - (size - 2 * pad) * 0.08 * (
            1 if i % 3 == 0 else 0)
        points.append((x, y))
    draw.line(points, fill=ACCENT, width=max(2, int(3 * scale)))
    return image


def build_ico() -> Path:
    target = ROOT / "assets" / "app.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_icon(size) for size in sizes]
    images[-1].save(target, format="ICO", sizes=[(s, s) for s in sizes])
    images[-1].save(ROOT / "assets" / "app.png", format="PNG")
    return target


def build_sample_csv() -> Path:
    from datetime import date

    from tradesome.app.bootstrap import build_container, shutdown
    from tradesome.data.providers.csv_provider import export_bars_csv
    from tradesome.data.providers.synthetic_provider import SyntheticProvider

    container = build_container(memory_db=True, with_demo=False)
    try:
        provider = SyntheticProvider(seed=42, registry=container.instruments)
        bars = provider.fetch_bars("600519.SH", date(2024, 1, 2), date(2024, 6, 28),
                                   "1d")
        target = ROOT / "assets" / "600519.SH.csv"
        export_bars_csv(bars[-60:], target)
        return target
    finally:
        shutdown(container)


if __name__ == "__main__":
    print("icon:", build_ico())
    print("csv:", build_sample_csv())
