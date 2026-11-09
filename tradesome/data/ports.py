"""行情数据源端口（Port）。

只要有 list_instruments / fetch_bars 两个方法，就能接入系统：
CSV、示例数据、未来的 tushare/akshare 适配器、甚至是内存里的测试桩。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Protocol, Union, runtime_checkable

from ..core.types import Bar, Instrument

DateLike = Union[date, datetime]


@runtime_checkable
class MarketDataProvider(Protocol):
    """行情数据源协议。"""

    name: str

    def list_instruments(self) -> List[Instrument]:
        """返回该数据源覆盖的标的。"""
        ...

    def fetch_bars(self, symbol: str, start: DateLike, end: DateLike,
                   timeframe: str = "1d") -> List[Bar]:
        """返回 [start, end] 内的 K 线，按时间升序。"""
        ...


class BaseProvider:
    """提供方基类：统一 name 与参数校验，子类只需实现两个方法。"""

    name = "base"

    def __init__(self, registry=None) -> None:
        self._registry = registry

    def _instrument(self, symbol: str) -> Optional[Instrument]:
        if self._registry is None:
            return None
        return self._registry.get(symbol)

    def __repr__(self) -> str:
        return "%s()" % type(self).__name__
