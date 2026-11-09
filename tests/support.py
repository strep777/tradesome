"""Shared helpers for the TradeSome test suite.

只在测试中使用：构造确定性的样本数据、临时工作区和内存容器。
"""

from __future__ import annotations

import math
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from tradesome.core.types import Bar

WORKSPACE = Path(__file__).resolve().parents[1]


def make_bars(symbol: str = "600519.SH", days: int = 260,
              start: date = date(2022, 1, 4), base: float = 100.0,
              drift: float = 0.0004, vol: float = 0.018,
              seed: int = 7, timeframe: str = "1d") -> List[Bar]:
    """生成形态可预期的日线：几何随机游走 + 20 日周期的温和波动。"""
    rng = random.Random(seed)
    bars: List[Bar] = []
    price = base
    day = start
    while len(bars) < days:
        if day.weekday() < 5:
            cycle = 0.0015 * math.sin(len(bars) / 6.0)
            change = drift + cycle + rng.gauss(0.0, vol)
            close = max(1.0, price * (1.0 + change))
            open_ = price * (1.0 + rng.gauss(0.0, vol / 3.0))
            high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, vol / 3.0)))
            low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, vol / 3.0)))
            volume = 1_000_000 * (1.0 + abs(rng.gauss(0.0, 0.35)))
            amount = volume * (open_ + close) / 2.0
            bars.append(Bar(symbol=symbol, dt=datetime.combine(day, datetime.min.time()),
                            open=round(open_, 2), high=round(high, 2),
                            low=round(low, 2), close=round(close, 2),
                            volume=round(volume), amount=round(amount, 2),
                            timeframe=timeframe))
            price = close
        day += timedelta(days=1)
    return bars


def make_trend_bars(symbol: str = "600519.SH", days: int = 260,
                    start: date = date(2022, 1, 4), base: float = 100.0,
                    step: float = 0.5, volume: int = 1_000_000) -> List[Bar]:
    """严格上涨的日线，方便断言趋势策略一定有持仓。"""
    bars: List[Bar] = []
    day = start
    price = base
    while len(bars) < days:
        if day.weekday() < 5:
            open_ = price
            price = price + step
            bars.append(Bar(symbol=symbol, dt=datetime.combine(day, datetime.min.time()),
                            open=open_, high=price + 0.2, low=open_ - 0.2,
                            close=price, volume=volume, amount=volume * price))
        day += timedelta(days=1)
    return bars


def bars_until(bars: Sequence[Bar], days: int) -> List[Bar]:
    return list(bars[:days])


def clone_bars(bars: Sequence[Bar], symbol: str) -> List[Bar]:
    return [Bar(symbol=symbol, dt=b.dt, open=b.open, high=b.high, low=b.low,
                close=b.close, volume=b.volume, amount=b.amount,
                timeframe=b.timeframe) for b in bars]


class TempWorkspace:
    """临时工作区：每个测试独立，不污染用户目录。"""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tradesome-test-")
        self.root = Path(self._tmp.name)

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def __enter__(self) -> "TempWorkspace":
        return self

    def __exit__(self, *_exc) -> None:
        self.cleanup()


def build_memory_container(root: Optional[Path] = None, with_demo: bool = False):
    from tradesome.app.bootstrap import build_container
    return build_container(root=root, memory_db=True, with_demo=with_demo)
