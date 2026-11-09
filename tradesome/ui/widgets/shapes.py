"""Canvas 圆角绘制：Tk 没有原生圆角，这里用多边形近似。"""

from __future__ import annotations

import math
import tkinter as tk


def rounded_points(x1: float, y1: float, x2: float, y2: float,
                   radius: float, steps: int = 16):
    """生成圆角矩形的顶点（顺时针，从左上角之后开始）。"""
    radius = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    points = []
    corners = (
        (x2 - radius, y1 + radius, -90.0, 0.0),
        (x2 - radius, y2 - radius, 0.0, 90.0),
        (x1 + radius, y2 - radius, 90.0, 180.0),
        (x1 + radius, y1 + radius, 180.0, 270.0),
    )
    for cx, cy, start, end in corners:
        for index in range(steps + 1):
            angle = math.radians(start + (end - start) * index / steps)
            points.extend((cx + radius * math.cos(angle),
                           cy + radius * math.sin(angle)))
    return points


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                 radius: float, **kwargs) -> int:
    """在 canvas 上画一个圆角矩形，返回 item id。"""
    return canvas.create_polygon(
        rounded_points(x1, y1, x2, y2, radius), smooth=False, **kwargs)
