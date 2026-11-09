"""行情仓储（带 LRU 缓存）。

回测/图表会反复请求同一标的的整段历史；这里缓存最近的 N 个
(symbol, timeframe) 组合，命中时直接在内存切区间，避免重复查库。
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ..core.types import Bar
from ..persistence.repositories import BarRepository as DbBarRepository
from .quality import QualityReport, check_bars

DateLike = Union[date, datetime]


class MarketDataRepository:
    """数据库 + 内存缓存。写操作会让相关缓存失效。"""

    def __init__(self, db_repo: DbBarRepository, cache_size: int = 8,
                 max_bars: int = 6000) -> None:
        self._db = db_repo
        self._cache: "OrderedDict[Tuple[str, str], List[Bar]]" = OrderedDict()
        self._cache_size = max(1, int(cache_size))
        self._max_bars = max(100, int(max_bars))
        self.hits = 0
        self.misses = 0

    # ---------------------------------------------------------- 读

    def get_bars(self, symbol: str, timeframe: str = "1d",
                 start: Optional[DateLike] = None, end: Optional[DateLike] = None,
                 limit: Optional[int] = None) -> List[Bar]:
        key = (symbol, timeframe)
        if start is None and end is None and key in self._cache:
            self.hits += 1
            self._cache.move_to_end(key)
            bars = list(self._cache[key])
            return bars[-int(limit):] if limit and limit > 0 else bars
        if key in self._cache and self._covers(self._cache[key], start, end):
            self.hits += 1
            self._cache.move_to_end(key)
            bars = self._slice(self._cache[key], start, end)
            return bars[-int(limit):] if limit and limit > 0 else bars
        self.misses += 1
        bars = self._db.get_bars(symbol, timeframe, start, end)
        if start is None and end is None:
            self._remember(key, bars)
        return bars[-int(limit):] if limit and limit > 0 else bars

    def get_bars_many(self, symbols: Sequence[str], timeframe: str = "1d",
                      start: Optional[DateLike] = None,
                      end: Optional[DateLike] = None,
                      limit: Optional[int] = None) -> Dict[str, List[Bar]]:
        return {s: self.get_bars(s, timeframe, start, end, limit) for s in symbols}

    def last_dt(self, symbol: str, timeframe: str = "1d") -> Optional[datetime]:
        if (symbol, timeframe) in self._cache:
            bars = self._cache[(symbol, timeframe)]
            return bars[-1].dt if bars else None
        return self._db.last_dt(symbol, timeframe)

    def coverage(self, symbol: str, timeframe: str = "1d"):
        return self._db.coverage(symbol, timeframe)

    def count(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> int:
        return self._db.count(symbol, timeframe)

    def symbols(self, timeframe: str = "1d") -> List[Dict]:
        return self._db.symbols(timeframe)

    # ---------------------------------------------------------- 写

    def upsert_bars(self, bars: Sequence[Bar]) -> int:
        written = self._db.upsert_bars(bars)
        for bar in bars:
            self._cache.pop((bar.symbol, bar.timeframe), None)
        return written

    def delete_symbol(self, symbol: str, timeframe: Optional[str] = None) -> int:
        removed = self._db.delete_symbol(symbol, timeframe)
        for key in [k for k in self._cache if k[0] == symbol]:
            if timeframe is None or key[1] == timeframe:
                self._cache.pop(key, None)
        return removed

    # ---------------------------------------------------------- 质量

    def quality(self, symbol: str, timeframe: str = "1d") -> QualityReport:
        bars = self.get_bars(symbol, timeframe)
        return check_bars(bars, symbol)

    # ---------------------------------------------------------- 缓存

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_info(self) -> Dict[str, int]:
        return {"size": len(self._cache), "capacity": self._cache_size,
                "hits": self.hits, "misses": self.misses}

    def _remember(self, key: Tuple[str, str], bars: List[Bar]) -> None:
        self._cache[key] = list(bars[-self._max_bars:])
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    @staticmethod
    def _as_date(value: DateLike) -> "date":
        """调用方既可能传 date 也可能传 datetime，统一成 date。"""
        return value.date() if isinstance(value, datetime) else value

    @classmethod
    def _covers(cls, bars: Sequence[Bar], start: Optional[DateLike],
                end: Optional[DateLike]) -> bool:
        if not bars:
            return False
        if start is not None and bars[0].dt.date() > cls._as_date(start):
            return False
        if end is not None and bars[-1].dt.date() < cls._as_date(end):
            return False
        return True

    @classmethod
    def _slice(cls, bars: Sequence[Bar], start: Optional[DateLike],
               end: Optional[DateLike]) -> List[Bar]:
        first = cls._as_date(start) if start is not None else None
        last = cls._as_date(end) if end is not None else None
        result: List[Bar] = []
        for bar in bars:
            day = bar.dt.date()
            if first is not None and day < first:
                continue
            if last is not None and day > last:
                continue
            result.append(bar)
        return result


# 兼容文档中的命名
BarRepository = MarketDataRepository
