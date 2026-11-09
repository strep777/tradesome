"""示例行情生成器：离线可用的"像真的"日线数据。

设计目标：不是随机游走，而是带市场因子 + 风格 beta + 状态切换的数据，
这样均线、动量、网格、RSI 等策略才会表现出明显不同的收益特征。
同一 seed + 同一代码永远生成同一段历史（可复现实验）。
"""

from __future__ import annotations

import math
import random
import zlib
from datetime import date, datetime, time
from typing import Dict, List, Optional, Sequence, Tuple

from ...core import calendar
from ...core.money import clamp, round_price
from ...core.types import KIND_ETF, Bar, FeeProfile, Instrument
from ..ports import BaseProvider
from ..resample import resample

ORIGIN = date(2021, 1, 4)   # 模拟上市起点
_MARKET_CACHE: Dict[Tuple[int, date], List[Tuple[date, float]]] = {}


def _ret(seed_key: int) -> random.Random:
    return random.Random(seed_key)


def _gauss(rng: random.Random) -> float:
    """Box-Muller，避免依赖 random.gauss 的实现细节。"""
    u1 = max(1e-12, rng.random())
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _market_returns(seed: int, end: date) -> List[Tuple[date, float]]:
    """全市场共同因子：四种状态轮换（牛/熊/震荡/高波动）。"""
    key = (int(seed), end)
    cached = _MARKET_CACHE.get(key)
    if cached is not None:
        return cached
    rng = _ret(int(seed) * 7919 + 17)
    days = calendar.trading_days(ORIGIN, end)
    # (日漂移, 日波动)，按年化换算
    regimes = {
        "bull": (0.12 / 244, 0.16 / math.sqrt(244)),
        "bear": (-0.14 / 244, 0.26 / math.sqrt(244)),
        "range": (0.01 / 244, 0.14 / math.sqrt(244)),
        "volatile": (0.0, 0.40 / math.sqrt(244)),
    }
    state = "bull"
    output: List[Tuple[date, float]] = []
    for _day in days:
        if rng.random() < 0.02:
            state = rng.choice(["bull", "bear", "range", "volatile"])
        drift, vol = regimes[state]
        value = drift + vol * _gauss(rng)
        # 单日指数波动限制在 ±4%，避免生成不可能的数据
        output.append((_day, clamp(value, -0.04, 0.04)))
    _MARKET_CACHE[key] = output
    if len(_MARKET_CACHE) > 32:
        _MARKET_CACHE.pop(next(iter(_MARKET_CACHE)))
    return output


def _profile(symbol: str, seed: int) -> Dict[str, float]:
    code = zlib.crc32(symbol.encode("utf-8")) & 0x7FFFFFFF
    rng = _ret(code + int(seed))
    return {
        "beta": 0.55 + rng.random() * 0.95,       # 0.55 ~ 1.50
        "idio": 0.006 + rng.random() * 0.016,     # 个股特异性波动
        "alpha": (rng.random() - 0.45) * 0.10 / 244,  # 微弱风格收益
        "start_price": 6.0 + (code % 18000) / 100.0,
        "base_volume": 2.0e6 + (code % 90) * 1.0e5,
    }


class SyntheticProvider(BaseProvider):
    """确定性示例数据源。"""

    name = "synthetic"

    def __init__(self, seed: int = 42, registry=None) -> None:
        super().__init__(registry)
        self.seed = int(seed)
        self._cache: Dict[str, List[Bar]] = {}

    # ---------------------------------------------------------- 端口实现

    def list_instruments(self) -> List[Instrument]:
        if self._registry is None:
            return []
        return [i for i in self._registry.all() if not i.is_index]

    def fetch_bars(self, symbol: str, start: date, end: date,
                   timeframe: str = "1d") -> List[Bar]:
        from ...core.instruments import normalize_symbol

        symbol = normalize_symbol(symbol)
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        bars = self._series(symbol, end_date)
        picked = [b for b in bars if start_date <= b.dt.date() <= end_date]
        if timeframe and timeframe != "1d":
            return resample(picked, timeframe)
        return picked

    def generate_all(self, symbols: Sequence[str], start: date, end: date,
                     timeframe: str = "1d") -> Dict[str, List[Bar]]:
        return {s: self.fetch_bars(s, start, end, timeframe) for s in symbols}

    # ---------------------------------------------------------- 生成

    def _series(self, symbol: str, end: date) -> List[Bar]:
        cached = self._cache.get(symbol)
        if cached and cached[-1].dt.date() >= end:
            return cached
        prof = _profile(symbol, self.seed)
        market = _market_returns(self.seed, end)
        rng = _ret((zlib.crc32(symbol.encode("utf-8")) + self.seed * 104729) & 0x7FFFFFFF)
        prev_close = round(prof["start_price"], 2)
        bars: List[Bar] = []
        for day, market_ret in market:
            if day < ORIGIN:
                continue
            # 隔夜跳空 + 日内趋势
            gap = clamp(_gauss(rng) * 0.004, -0.02, 0.02)
            open_price = round_price(prev_close * (1.0 + gap), 0.01)
            open_price = max(0.5, open_price)
            daily = (prof["beta"] * market_ret + prof["alpha"]
                     + prof["idio"] * _gauss(rng))
            daily = clamp(daily, -0.099, 0.099)
            close_price = max(0.5, round_price(open_price * (1.0 + daily), 0.01))
            # 日内影线
            upper = abs(_gauss(rng)) * 0.008 + 0.002
            lower = abs(_gauss(rng)) * 0.008 + 0.002
            high = round_price(max(open_price, close_price) * (1.0 + upper), 0.01)
            low = round_price(min(open_price, close_price) * (1.0 - lower), 0.01)
            high = max(high, open_price, close_price)
            low = max(0.5, min(low, open_price, close_price))
            volume = prof["base_volume"] * math.exp(_gauss(rng) * 0.35)
            volume *= 1.0 + min(4.0, abs(daily) / 0.02)
            volume = float(int(volume // 100) * 100)
            amount = round(volume * (open_price + high + low + close_price) / 4.0, 2)
            bars.append(Bar(
                symbol=symbol,
                dt=datetime.combine(day, time(15, 0)),
                open=open_price, high=high, low=low, close=close_price,
                volume=volume, amount=amount, timeframe="1d",
            ))
            prev_close = close_price
        self._cache[symbol] = bars
        return bars

    def invalidate(self, symbol: Optional[str] = None) -> None:
        if symbol:
            self._cache.pop(symbol, None)
        else:
            self._cache.clear()


def make_synthetic_instrument(symbol: str, name: str = "", kind: str = "STOCK") -> Instrument:
    """生成与示例数据匹配的标的（ETF 无印花税）。"""
    from ...core.instruments import make_instrument

    return make_instrument(symbol, name, kind)
