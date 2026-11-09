"""金额与数值工具。

统一舍入口径：四舍五入（ROUND_HALF_UP），保留两位小数。
禁止在业务代码中手写 round(x, 2)，避免银行家舍入导致的对账差异。
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

_CENT = Decimal("0.01")


def round_money(value: float) -> float:
    """金额舍入到分（四舍五入）。"""
    if value is None or not math.isfinite(float(value)):
        return 0.0
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def round_price(value: float, tick: float = 0.01) -> float:
    """按最小价位对齐（四舍五入到 tick 的整数倍）。"""
    if value is None or not math.isfinite(float(value)):
        return 0.0
    tick = float(tick) if tick and tick > 0 else 0.01
    steps = Decimal(str(float(value))) / Decimal(str(tick))
    return float(steps.quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(tick)))


def floor_price(value: float, tick: float = 0.01) -> float:
    """向下对齐到最小价位（买入滑点用）。"""
    tick = float(tick) if tick and tick > 0 else 0.01
    steps = math.floor(float(value) / tick + 1e-9)
    return float(round(steps * tick, 6))


def ceil_price(value: float, tick: float = 0.01) -> float:
    """向上对齐到最小价位（卖出滑点用）。"""
    tick = float(tick) if tick and tick > 0 else 0.01
    steps = math.ceil(float(value) / tick - 1e-9)
    return float(round(steps * tick, 6))


def pct(numerator: float, denominator: float, default: float = 0.0) -> float:
    """比例：numerator / denominator。"""
    if not denominator:
        return default
    return float(numerator) / float(denominator)


def change_pct(current: float, previous: float) -> float:
    """涨跌幅：current/previous - 1。"""
    if not previous:
        return 0.0
    return float(current) / float(previous) - 1.0


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not denominator:
        return default
    return float(numerator) / float(denominator)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def to_float(value, default: Optional[float] = None) -> Optional[float]:
    """尽力转 float；失败返回 default。"""
    if value is None:
        return default
    try:
        result = float(str(value).replace(",", "").replace("¥", "").strip())
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def stdev(values) -> float:
    """样本标准差（无偏）。"""
    data = [float(v) for v in values]
    n = len(data)
    if n < 2:
        return 0.0
    avg = sum(data) / n
    variance = sum((x - avg) ** 2 for x in data) / (n - 1)
    return math.sqrt(max(0.0, variance))
