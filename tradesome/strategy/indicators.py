"""常用技术指标（纯标准库、等长输出、预热期为 nan）。

所有函数遵循同一约定：
- 输入是时间正序的序列，输出与输入等长；
- 数据不足时对应位置为 float("nan")，调用方用 strategy.base.last() 取值；
- 不修改输入。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from ..core.types import Bar

NAN = float("nan")


def _empty(n: int) -> List[float]:
    return [NAN] * n


def is_valid(value: Optional[float]) -> bool:
    if value is None:
        return False
    try:
        return value == value and not math.isinf(value)
    except TypeError:
        return False


def closes(bars: Sequence[Bar]) -> List[float]:
    return [b.close for b in bars]


# ---------------------------------------------------------------- 均线类


def sma(values: Sequence[float], period: int) -> List[float]:
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n < period:
        return out
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> List[float]:
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    prev = float(values[0])
    out[0] = prev
    for i in range(1, n):
        prev = alpha * float(values[i]) + (1 - alpha) * prev
        out[i] = prev
    return out


def wma(values: Sequence[float], period: int) -> List[float]:
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n < period:
        return out
    weight = period * (period + 1) / 2.0
    for i in range(period - 1, n):
        total = 0.0
        for j in range(period):
            total += values[i - j] * (period - j)
        out[i] = total / weight
    return out


# ---------------------------------------------------------------- 摆动类


def rsi(values: Sequence[float], period: int = 14) -> List[float]:
    """Wilder RSI，范围 0–100。"""
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9):
    """返回 (DIF, DEA, MACD柱)；柱值按国内习惯乘 2。"""
    n = len(values)
    if n == 0:
        return [], [], []
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    dif = [fast_ema[i] - slow_ema[i] for i in range(n)]
    dea = ema(dif, signal)
    hist = [(dif[i] - dea[i]) * 2.0 for i in range(n)]
    return dif, dea, hist


def kdj(bars: Sequence[Bar], n: int = 9, k_period: int = 3,
        d_period: int = 3):
    """返回 (K, D, J)，初始值 50。"""
    count = len(bars)
    k_out = _empty(count)
    d_out = _empty(count)
    j_out = _empty(count)
    if count == 0:
        return k_out, d_out, j_out
    n = max(1, int(n))
    k_prev = 50.0
    d_prev = 50.0
    for i in range(count):
        start = max(0, i - n + 1)
        window = bars[start:i + 1]
        highest = max(b.high for b in window)
        lowest = min(b.low for b in window)
        rsv = 50.0 if highest == lowest else (bars[i].close - lowest) / (highest - lowest) * 100.0
        k_prev = ((k_period - 1) * k_prev + rsv) / k_period
        d_prev = ((d_period - 1) * d_prev + k_prev) / d_period
        k_out[i] = k_prev
        d_out[i] = d_prev
        j_out[i] = 3 * k_prev - 2 * d_prev
    return k_out, d_out, j_out


def roc(values: Sequence[float], period: int = 12) -> List[float]:
    """变动率，百分比。"""
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0:
        return out
    for i in range(period, n):
        base = values[i - period]
        if base:
            out[i] = (values[i] / base - 1.0) * 100.0
    return out


# ---------------------------------------------------------------- 通道/波动


def boll(values: Sequence[float], period: int = 20, k: float = 2.0):
    """返回 (中轨, 上轨, 下轨)，使用总体标准差。"""
    n = len(values)
    mid = sma(values, period)
    std = stddev(values, period)
    upper = _empty(n)
    lower = _empty(n)
    for i in range(n):
        if is_valid(mid[i]) and is_valid(std[i]):
            upper[i] = mid[i] + k * std[i]
            lower[i] = mid[i] - k * std[i]
    return mid, upper, lower


def stddev(values: Sequence[float], period: int) -> List[float]:
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n < period:
        return out
    total = 0.0
    total_sq = 0.0
    for i, value in enumerate(values):
        total += value
        total_sq += value * value
        if i >= period:
            old = values[i - period]
            total -= old
            total_sq -= old * old
        if i >= period - 1:
            mean = total / period
            var = max(0.0, total_sq / period - mean * mean)
            out[i] = math.sqrt(var)
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> List[float]:
    """平均真实波幅（Wilder 平滑）。"""
    n = len(bars)
    out = _empty(n)
    period = int(period)
    if n == 0 or period <= 0:
        return out
    trs: List[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if n < period:
        return out
    value = sum(trs[:period]) / period
    out[period - 1] = value
    for i in range(period, n):
        value = (value * (period - 1) + trs[i]) / period
        out[i] = value
    return out


def donchian(bars: Sequence[Bar], period: int = 20):
    """返回 (上轨=最高价, 下轨=最低价)，含当前 bar。"""
    n = len(bars)
    upper = _empty(n)
    lower = _empty(n)
    period = int(period)
    if period <= 0 or n < period:
        return upper, lower
    for i in range(period - 1, n):
        window = bars[i - period + 1:i + 1]
        upper[i] = max(b.high for b in window)
        lower[i] = min(b.low for b in window)
    return upper, lower


def highest(values: Sequence[float], period: int) -> List[float]:
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        out[i] = max(values[i - period + 1:i + 1])
    return out


def lowest(values: Sequence[float], period: int) -> List[float]:
    n = len(values)
    out = _empty(n)
    period = int(period)
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        out[i] = min(values[i - period + 1:i + 1])
    return out


def obv(bars: Sequence[Bar]) -> List[float]:
    """能量潮：涨日加量、跌日减量。"""
    n = len(bars)
    out = _empty(n)
    if n == 0:
        return out
    total = 0.0
    out[0] = total
    for i in range(1, n):
        if bars[i].close > bars[i - 1].close:
            total += bars[i].volume
        elif bars[i].close < bars[i - 1].close:
            total -= bars[i].volume
        out[i] = total
    return out


def volatility(values: Sequence[float], period: int = 20) -> List[float]:
    """收益率的滚动标准差（日波动率）。"""
    n = len(values)
    out = _empty(n)
    if n < 2:
        return out
    returns = [NAN]
    for i in range(1, n):
        base = values[i - 1]
        returns.append((values[i] / base - 1.0) if base else 0.0)
    period = max(2, int(period))
    for i in range(period, n):
        window = [r for r in returns[i - period + 1:i + 1] if is_valid(r)]
        if len(window) >= 2:
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / (len(window) - 1)
            out[i] = math.sqrt(var)
    return out
