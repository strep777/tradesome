"""模拟券商（模拟盘）：与回测共用撮合规则，但逐根 bar 实时推进。"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..core.events import ORDER_FILLED, ORDER_REJECTED
from ..core.logging import get_logger
from ..core.types import ST_CANCELLED, ST_FILLED, ST_PARTIAL, ST_REJECTED, Bar, Fill, Order
from ..backtest.matching import MatchingEngine
from ..portfolio.account import Account

LOGGER = get_logger("execution.paper")


class PaperBroker:
    name = "paper"

    def __init__(self, account: Account, instruments, fee,
                 max_volume_ratio: float = 0.05, bus=None) -> None:
        self.account = account
        self.instruments = instruments
        self.matcher = MatchingEngine(fee, max_volume_ratio)
        self.bus = bus
        self._lock = threading.RLock()
        self._pending: Dict[str, Order] = {}
        self._orders: Dict[str, Order] = {}
        self._last_close: Dict[str, float] = {}

    # Broker 协议

    def submit(self, order: Order, price: float) -> Order:
        with self._lock:
            if order.side == "BUY":
                estimate = price * order.qty * 1.001 + 5.0
                self.account.freeze(order.order_id, min(estimate, self.account.cash))
            self._pending[order.order_id] = order
            self._orders[order.order_id] = order
            return order

    def cancel(self, order_id: str) -> bool:
        with self._lock:
            order = self._pending.pop(order_id, None)
            if order is None:
                return False
            self.account.release(order_id)
            self._orders[order_id] = order.evolve(
                status=ST_CANCELLED, updated_at=datetime.now())
            return True

    def open_orders(self) -> List[Order]:
        with self._lock:
            return list(self._pending.values())

    def all_orders(self) -> List[Order]:
        with self._lock:
            return list(self._orders.values())

    # 行情推进

    def on_bar(self, bar: Bar) -> List[Tuple[Order, Fill]]:
        with self._lock:
            instrument = self.instruments.get(bar.symbol)
            if instrument is None:
                return []
            results: List[Tuple[Order, Fill]] = []
            prev_close = self._last_close.get(bar.symbol)
            for order_id in list(self._pending.keys()):
                order = self._pending.get(order_id)
                if order is None or order.symbol != bar.symbol:
                    continue
                outcome = self.matcher.match(
                    order, bar, instrument, self.account.cash,
                    self.account.available_qty(bar.symbol), prev_close=prev_close)
                if outcome.rejected:
                    self._pending.pop(order_id, None)
                    self.account.release(order_id)
                    updated = order.evolve(status=ST_REJECTED, updated_at=bar.dt,
                                           reason=outcome.reason or "撮合失败")
                    self._orders[order_id] = updated
                    self._emit(ORDER_REJECTED, updated, outcome.reason)
                    continue
                if outcome.fill is None:
                    continue
                fill = outcome.fill
                self.account.release(order_id)
                self.account.apply_fill(fill)
                remain = int(order.remaining) - fill.qty
                filled = int(order.filled_qty) + fill.qty
                avg = ((order.avg_fill_price * order.filled_qty + fill.price * fill.qty)
                       / filled) if filled else fill.price
                updated = order.evolve(
                    status=ST_FILLED if remain <= 0 else ST_PARTIAL,
                    filled_qty=filled, avg_fill_price=avg, updated_at=bar.dt)
                self._orders[order.order_id] = updated
                if remain <= 0:
                    self._pending.pop(order_id, None)
                else:
                    self._pending[order_id] = updated
                results.append((updated, fill))
                self._emit(ORDER_FILLED, updated, fill.price, fill=fill)
            self._last_close[bar.symbol] = bar.close
            return results

    def _emit(self, topic: str, order: Order, message, **extra) -> None:
        if self.bus is None:
            return
        payload = {"order": order, "message": str(message)}
        payload.update(extra)
        self.bus.emit(topic, **payload)
