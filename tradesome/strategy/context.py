"""策略上下文：策略与系统之间的唯一通道。

策略通过 ctx 下单、取历史、读指标；它拿不到数据库、账户对象或网络，
因此无法绕过风控，也无法看到"未来"的数据（market 视图只到当前 bar）。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

from ..core.errors import ValidationError
from ..core.ids import client_order_id
from ..core.logging import get_logger
from ..core.types import ORDER_LIMIT, ORDER_MARKET, SIDE_BUY, SIDE_SELL, Bar, Order, OrderRequest, Position
from . import indicators as ta

LOGGER = get_logger("strategy.context")


@runtime_checkable
class TradingGateway(Protocol):
    def submit(self, request: OrderRequest, strategy_id: str = "",
               session_id: str = "") -> Optional[Order]:
        ...

    def cancel(self, order_id: str) -> bool:
        ...


@runtime_checkable
class AccountView(Protocol):
    @property
    def cash(self) -> float:
        ...

    @property
    def equity(self) -> float:
        ...

    @property
    def positions(self) -> Sequence[Position]:
        ...

    def position_of(self, symbol: str) -> Optional[Position]:
        ...

    def available_qty(self, symbol: str) -> int:
        ...


@runtime_checkable
class MarketView(Protocol):
    def history(self, symbol: str, n: Optional[int] = None,
                field: str = "close") -> List[float]:
        ...

    def bars(self, symbol: str, n: Optional[int] = None) -> List[Bar]:
        ...

    def latest(self, symbol: str) -> Optional[Bar]:
        ...

    def now(self) -> datetime:
        ...


class IndicatorAPI:
    """按需计算指标；同一根 bar 内重复调用走缓存。"""

    def __init__(self, ctx: "StrategyContext") -> None:
        self._ctx = ctx
        self._cache: Dict[Tuple, Any] = {}
        self._stamp = None

    def _check_stamp(self) -> None:
        stamp = self._ctx.stamp
        if stamp != self._stamp:
            self._cache.clear()
            self._stamp = stamp

    def _series(self, symbol: str, field: str, need: int) -> List[float]:
        return self._ctx.history(symbol, n=max(need, 30), field=field)

    def _bars(self, symbol: str, need: int) -> List[Bar]:
        return self._ctx.bars(symbol, n=max(need, 30))

    def _cached(self, key: Tuple, producer):
        self._check_stamp()
        if key not in self._cache:
            self._cache[key] = producer()
        return self._cache[key]

    # ---------------------------------------------------------- 均线

    def sma(self, symbol: str, period: int = 20, field: str = "close") -> List[float]:
        key = ("sma", symbol, period, field)
        return self._cached(key, lambda: ta.sma(self._series(symbol, field, period * 3), period))

    def ema(self, symbol: str, period: int = 20, field: str = "close") -> List[float]:
        key = ("ema", symbol, period, field)
        return self._cached(key, lambda: ta.ema(self._series(symbol, field, period * 3), period))

    def wma(self, symbol: str, period: int = 20, field: str = "close") -> List[float]:
        key = ("wma", symbol, period, field)
        return self._cached(key, lambda: ta.wma(self._series(symbol, field, period * 3), period))

    # ---------------------------------------------------------- 摆动

    def rsi(self, symbol: str, period: int = 14, field: str = "close") -> List[float]:
        key = ("rsi", symbol, period, field)
        return self._cached(key, lambda: ta.rsi(self._series(symbol, field, period * 4), period))

    def macd(self, symbol: str, fast: int = 12, slow: int = 26,
             signal: int = 9, field: str = "close"):
        key = ("macd", symbol, fast, slow, signal, field)
        return self._cached(
            key, lambda: ta.macd(self._series(symbol, field, slow * 4), fast, slow, signal))

    def kdj(self, symbol: str, n: int = 9, k_period: int = 3, d_period: int = 3):
        key = ("kdj", symbol, n, k_period, d_period)
        return self._cached(key, lambda: ta.kdj(self._bars(symbol, n * 5), n, k_period, d_period))

    def roc(self, symbol: str, period: int = 12, field: str = "close") -> List[float]:
        key = ("roc", symbol, period, field)
        return self._cached(key, lambda: ta.roc(self._series(symbol, field, period * 4), period))

    # ---------------------------------------------------------- 通道/波动

    def boll(self, symbol: str, period: int = 20, k: float = 2.0,
             field: str = "close"):
        key = ("boll", symbol, period, k, field)
        return self._cached(
            key, lambda: ta.boll(self._series(symbol, field, period * 4), period, k))

    def atr(self, symbol: str, period: int = 14) -> List[float]:
        key = ("atr", symbol, period)
        return self._cached(key, lambda: ta.atr(self._bars(symbol, period * 4), period))

    def donchian(self, symbol: str, period: int = 20):
        key = ("donchian", symbol, period)
        return self._cached(key, lambda: ta.donchian(self._bars(symbol, period * 3), period))

    def std(self, symbol: str, period: int = 20, field: str = "close") -> List[float]:
        key = ("std", symbol, period, field)
        return self._cached(
            key, lambda: ta.stddev(self._series(symbol, field, period * 3), period))

    def highest(self, symbol: str, period: int = 20, field: str = "close") -> List[float]:
        key = ("highest", symbol, period, field)
        return self._cached(
            key, lambda: ta.highest(self._series(symbol, field, period * 3), period))

    def lowest(self, symbol: str, period: int = 20, field: str = "close") -> List[float]:
        key = ("lowest", symbol, period, field)
        return self._cached(
            key, lambda: ta.lowest(self._series(symbol, field, period * 3), period))

    def obv(self, symbol: str) -> List[float]:
        key = ("obv", symbol)
        return self._cached(key, lambda: ta.obv(self._bars(symbol, 120)))

    def volatility(self, symbol: str, period: int = 20) -> List[float]:
        key = ("vol", symbol, period)
        return self._cached(
            key, lambda: ta.volatility(self._series(symbol, "close", period * 4), period))


class StrategyContext:
    def __init__(self, strategy_id: str, session_id: str, gateway: TradingGateway,
                 account: AccountView, market: MarketView,
                 instrument_lookup=None) -> None:
        self.strategy_id = strategy_id
        self.session_id = session_id
        self.gateway = gateway
        self.account = account
        self.market = market
        self._lookup = instrument_lookup
        self.indicator = IndicatorAPI(self)
        self.stamp: Any = None          # 当前 bar 时间，引擎每根 bar 更新
        self.allow_trading = True       # 预热/只读模式下为 False，信号被丢弃
        self.buy_filter = None          # 可选：返回 False 时拦截新的买入（不拦卖出）
        self.logs: List[str] = []
        self._order_seq = 0
        self._bar_orders: Dict[Tuple[str, str], str] = {}
        self._seen: Dict[Tuple[str, str, Any], str] = {}

    # ---------------------------------------------------------- 生命周期

    def begin_bar(self, ts: Any) -> None:
        self.stamp = ts
        self._bar_orders.clear()

    # ---------------------------------------------------------- 账户

    @property
    def cash(self) -> float:
        return self.account.cash

    @property
    def equity(self) -> float:
        return self.account.equity

    @property
    def positions(self) -> Sequence[Position]:
        return self.account.positions

    def position_of(self, symbol: str) -> Optional[Position]:
        return self.account.position_of(symbol)

    def available(self, symbol: str) -> int:
        return self.account.available_qty(symbol)

    # ---------------------------------------------------------- 行情

    def bar(self, symbol: str) -> Optional[Bar]:
        return self.market.latest(symbol)

    def price(self, symbol: str) -> Optional[float]:
        bar = self.market.latest(symbol)
        return bar.close if bar is not None else None

    def bars(self, symbol: str, n: Optional[int] = None) -> List[Bar]:
        return self.market.bars(symbol, n)

    def history(self, symbol: str, n: Optional[int] = None,
                field: str = "close") -> List[float]:
        return self.market.history(symbol, n=n, field=field)

    def now(self) -> datetime:
        return self.market.now()

    # ---------------------------------------------------------- 下单

    def buy(self, symbol: str, weight: Optional[float] = None,
            qty: Optional[int] = None, limit_price: Optional[float] = None,
            reason: str = "") -> Optional[Order]:
        return self._place(symbol, SIDE_BUY, weight, qty, limit_price, reason)

    def sell(self, symbol: str, weight: Optional[float] = None,
             qty: Optional[int] = None, limit_price: Optional[float] = None,
             reason: str = "") -> Optional[Order]:
        return self._place(symbol, SIDE_SELL, weight, qty, limit_price, reason)

    def close(self, symbol: str, reason: str = "策略平仓") -> Optional[Order]:
        position = self.account.position_of(symbol)
        if position is None or position.available_qty <= 0:
            return None
        return self._place(symbol, SIDE_SELL, None, position.available_qty, None, reason)

    def close_all(self, reason: str = "策略清仓") -> List[Order]:
        orders: List[Order] = []
        for position in list(self.account.positions):
            order = self.close(position.symbol, reason=reason)
            if order is not None:
                orders.append(order)
        return orders

    def _place(self, symbol: str, side: str, weight: Optional[float],
               qty: Optional[int], limit_price: Optional[float],
               reason: str) -> Optional[Order]:
        if not self.allow_trading:
            return None
        if side == SIDE_BUY and self.buy_filter is not None and not self.buy_filter(symbol):
            self.log("标的 %s 未通过买入过滤，忽略开仓信号" % symbol, level="DEBUG")
            return None
        bar = self.market.latest(symbol)
        if bar is None:
            self.log("标的 %s 当前没有行情，忽略下单" % symbol, level="WARN")
            return None
        instrument = None
        if self._lookup is not None:
            instrument = self._lookup(symbol)
        price = float(limit_price) if limit_price else float(bar.close)
        if price <= 0:
            return None
        if qty is None:
            qty = self._size_order(symbol, side, weight, price, instrument)
        if qty is None or qty <= 0:
            return None
        lot = instrument.lot_size if instrument is not None else 100
        if qty % lot != 0:
            qty = (qty // lot) * lot
            if qty <= 0:
                return None
        key = (symbol, side)
        if key in self._bar_orders:
            self.log("同一根 K 线内 %s %s 重复下单已忽略（幂等保护）" % (symbol, side), level="DEBUG")
            return None
        self._order_seq += 1
        client_id = client_order_id(self.strategy_id, symbol, side, bar.dt, 0)
        request = OrderRequest(
            client_id=client_id, symbol=symbol, side=side, qty=int(qty),
            order_type=ORDER_LIMIT if limit_price else ORDER_MARKET,
            limit_price=float(limit_price) if limit_price else None,
            reason=reason,
        )
        order = self.gateway.submit(request, strategy_id=self.strategy_id,
                                    session_id=self.session_id)
        if order is not None:
            self._bar_orders[key] = order.order_id
        return order

    def _size_order(self, symbol: str, side: str, weight: Optional[float],
                    price: float, instrument) -> Optional[int]:
        lot = instrument.lot_size if instrument is not None else 100
        if side == SIDE_SELL:
            position = self.account.position_of(symbol)
            if position is None:
                return None
            if weight is None:
                return position.available_qty
            target = int((position.available_qty * max(0.0, min(1.0, weight))) // lot) * lot
            return target
        if weight is None:
            weight = 0.10
        weight = float(weight)
        if weight <= 0:
            return None
        budget = min(self.account.equity * weight, self.account.cash * 0.995)
        if budget <= 0:
            return None
        lots = int(budget / (price * lot))
        return lots * lot

    # ---------------------------------------------------------- 日志

    def log(self, message: str, level: str = "INFO") -> None:
        text = "[%s] %s" % (self.strategy_id, message)
        self.logs.append(text)
        if len(self.logs) > 500:
            del self.logs[:100]
        LOGGER.log(getattr(logging, level.upper(), logging.INFO), "%s", text)
