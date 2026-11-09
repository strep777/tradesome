"""给实盘策略的只读行情视图。

策略看到的最后一根 K 线是"今天 + 最新价"合成的，用来在 14:55 做当日决策；
历史部分仍是本地日线，不会被未来数据污染。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..core.errors import DataError
from ..core.types import Bar


class LiveMarket:
    def __init__(self, bars_by_symbol: Dict[str, List[Bar]],
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self._bars: Dict[str, List[Bar]] = {
            symbol: list(series) for symbol, series in bars_by_symbol.items()
        }
        self._prices: Dict[str, float] = {}
        self._clock = clock or datetime.now

    # ---------------------------------------------------------- 行情注入

    def set_price(self, symbol: str, price: float) -> None:
        if price and price > 0:
            self._prices[symbol] = float(price)

    def set_bars(self, symbol: str, bars: List[Bar]) -> None:
        self._bars[symbol] = list(bars)

    def symbols(self) -> List[str]:
        return sorted(self._bars)

    # ---------------------------------------------------------- MarketView

    def latest(self, symbol: str) -> Optional[Bar]:
        series = self._bars.get(symbol) or []
        last = series[-1] if series else None
        price = self._prices.get(symbol)
        if price is None or price <= 0:
            return last
        if last is None:
            return Bar(symbol=symbol, dt=self._clock(), open=price, high=price,
                       low=price, close=price, volume=0.0, amount=0.0)
        if abs(last.close - price) < 1e-9:
            return last
        return Bar(
            symbol=symbol, dt=last.dt, open=last.open,
            high=max(last.high, price), low=min(last.low, price),
            close=price, volume=last.volume, amount=last.amount,
        )

    def bars(self, symbol: str, n: Optional[int] = None) -> List[Bar]:
        series = self._bars.get(symbol) or []
        if series:
            series = list(series)
            live = self.latest(symbol)
            if live is not None and live is not series[-1]:
                series[-1] = live
        else:
            live = self.latest(symbol)
            series = [live] if live is not None else []
        if n is not None and n > 0:
            series = series[-int(n):]
        return series

    def history(self, symbol: str, n: Optional[int] = None,
                field: str = "close") -> List[float]:
        if field not in ("open", "high", "low", "close", "volume", "amount"):
            raise DataError("不支持的行情字段：%s" % field)
        return [float(getattr(bar, field)) for bar in self.bars(symbol, n)]

    def now(self) -> datetime:
        return self._clock()
