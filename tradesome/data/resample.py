"""K 线周期聚合：日线 → 周线 / 月线。

规则：open 取首根、high 取最大、low 取最小、close 取末根、量额求和；
时间戳取该周期最后一根 K 线的时间（与"下一 bar 开盘成交"的撮合语义一致）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Sequence

from ..core.money import round_money
from ..core.types import Bar

SUPPORTED = ("1w", "1M")


def _bucket_key(dt: datetime, timeframe: str):
    if timeframe == "1w":
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    if timeframe == "1M":
        return (dt.year, dt.month)
    raise ValueError("不支持的聚合周期：%s" % timeframe)


def aggregate(bars: Sequence[Bar], timeframe: str) -> List[Bar]:
    """把日线聚合为周线/月线。输入必须按时间升序。"""
    if timeframe not in SUPPORTED:
        raise ValueError("不支持的聚合周期：%s" % timeframe)
    if not bars:
        return []
    result: List[Bar] = []
    current_key = None
    group: List[Bar] = []

    def flush() -> None:
        if not group:
            return
        first, last = group[0], group[-1]
        result.append(Bar(
            symbol=last.symbol,
            dt=last.dt,
            open=first.open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=last.close,
            volume=round_money(sum(b.volume for b in group)),
            amount=round_money(sum(b.amount for b in group)),
            timeframe=timeframe,
        ))

    for bar in bars:
        key = _bucket_key(bar.dt, timeframe)
        if current_key is None:
            current_key = key
        elif key != current_key:
            flush()
            group = []
            current_key = key
        group.append(bar)
    flush()
    return result


def to_weekly(bars: Sequence[Bar]) -> List[Bar]:
    return aggregate(bars, "1w")


def to_monthly(bars: Sequence[Bar]) -> List[Bar]:
    return aggregate(bars, "1M")


def resample(bars: Sequence[Bar], timeframe: str) -> List[Bar]:
    """按目标周期返回 K 线；目标为 1d 时原样返回。"""
    if timeframe in ("1d", "", None):
        return list(bars)
    return aggregate(bars, timeframe)


def resample_many(data: Dict[str, Sequence[Bar]], timeframe: str) -> Dict[str, List[Bar]]:
    return {symbol: resample(bars, timeframe) for symbol, bars in data.items()}


def infer_timeframe(bars: Sequence[Bar]) -> str:
    """根据相邻间隔推断周期（用于导入数据自检）。"""
    if not bars:
        return ""
    if len(bars) < 2:
        return bars[0].timeframe or "1d"
    deltas = [(bars[i].dt - bars[i - 1].dt).total_seconds() for i in range(1, len(bars))]
    median = sorted(deltas)[len(deltas) // 2]
    if median >= 25 * 86400:
        return "1M"
    if median >= 6 * 86400:
        return "1w"
    if median >= 20 * 3600:
        return "1d"
    minutes = median / 60.0
    for label, size in (("60m", 60), ("30m", 30), ("15m", 15), ("5m", 5), ("1m", 1)):
        if abs(minutes - size) <= max(1.0, size * 0.2):
            return label
    return "1d"


def bars_to_series(bars: Iterable[Bar], field: str = "close") -> List[float]:
    return [float(getattr(b, field)) for b in bars]
