"""订单状态机（OMS）。

本地订单（``client_id``，我们生成）与柜台订单（``broker_order_id``）是两套编号，
OMS 负责把它们对齐，并把柜台的回报翻译成统一的 ``Order``：

* 只接受合法的状态迁移，乱序回报不会把已成交的订单改回"已报"；
* 成交按 ``trade_id`` 去重，断线重连后补推的成交不会重复记账；
* 断线时无法确认的在场委托标为 ``UNKNOWN``，由对账/人工处理，绝不假装成功。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict, List, Optional

from ..core.types import (
    ST_CANCELLED, ST_FILLED, ST_PARTIAL, ST_PENDING, ST_REJECTED, ST_SUBMITTED,
    ST_UNKNOWN, Fill, Order,
)
from ..gateway.protocol import OrderUpdate, TradeUpdate

STATUS_MAP = {
    "PENDING": ST_PENDING,
    "SUBMITTED": ST_SUBMITTED,
    "NOTTRADED": ST_SUBMITTED,
    "NOT_TRADED": ST_SUBMITTED,
    "PARTIAL": ST_PARTIAL,
    "PARTTRADED": ST_PARTIAL,
    "PART_TRADED": ST_PARTIAL,
    "FILLED": ST_FILLED,
    "ALLTRADED": ST_FILLED,
    "ALL_TRADED": ST_FILLED,
    "CANCELLED": ST_CANCELLED,
    "CANCELED": ST_CANCELLED,
    "REJECTED": ST_REJECTED,
    "UNKNOWN": ST_UNKNOWN,
}

# 允许的状态迁移（终止态不再变化，UNKNOWN 可被回报修正）
ALLOWED = {
    ST_PENDING: {ST_SUBMITTED, ST_PARTIAL, ST_FILLED, ST_CANCELLED, ST_REJECTED, ST_UNKNOWN},
    ST_SUBMITTED: {ST_PARTIAL, ST_FILLED, ST_CANCELLED, ST_REJECTED, ST_UNKNOWN},
    ST_PARTIAL: {ST_FILLED, ST_CANCELLED, ST_UNKNOWN},
    ST_UNKNOWN: {ST_SUBMITTED, ST_PARTIAL, ST_FILLED, ST_CANCELLED, ST_REJECTED},
    ST_FILLED: set(),
    ST_CANCELLED: set(),
    ST_REJECTED: set(),
}


def map_status(raw: str) -> str:
    return STATUS_MAP.get(str(raw or "").upper(), ST_UNKNOWN)


_RANK = {
    ST_UNKNOWN: 0, ST_PENDING: 1, ST_SUBMITTED: 2, ST_PARTIAL: 3,
    ST_CANCELLED: 4, ST_REJECTED: 4, ST_FILLED: 5,
}


class OrderManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_client: Dict[str, Order] = {}
        self._by_broker: Dict[str, Order] = {}
        self._by_id: Dict[str, Order] = {}
        self._trade_ids: set = set()
        self._fills: List[Fill] = []

    # ---------------------------------------------------------- 登记与查询

    def register(self, order: Order) -> Order:
        with self._lock:
            existing = (self._by_client.get(order.client_id)
                        or self._by_broker.get(order.broker_order_id or ""))
            if existing is not None and _RANK.get(existing.status, 0) > _RANK.get(order.status, 0):
                # 同步回报可能早于 submit 返回，不能把已成交的单覆盖回"已报"
                order = order.evolve(
                    status=existing.status,
                    filled_qty=max(existing.filled_qty, order.filled_qty),
                    avg_fill_price=existing.avg_fill_price or order.avg_fill_price,
                    broker_order_id=existing.broker_order_id or order.broker_order_id,
                    updated_at=existing.updated_at,
                    reason=existing.reason or order.reason,
                )
            self._by_client[order.client_id] = order
            self._by_id[order.order_id] = order
            if order.broker_order_id:
                self._by_broker[order.broker_order_id] = order
            return order

    def get(self, order_id: str) -> Optional[Order]:
        """按本地订单号查询；兼容按 client_id 查询的旧调用。"""
        with self._lock:
            return self._by_id.get(order_id) or self._by_client.get(order_id)

    def by_broker(self, broker_order_id: str) -> Optional[Order]:
        with self._lock:
            return self._by_broker.get(broker_order_id)

    def all(self) -> List[Order]:
        with self._lock:
            return list(self._by_client.values())

    def open_orders(self) -> List[Order]:
        with self._lock:
            return [o for o in self._by_client.values() if o.is_open]

    def fills(self) -> List[Fill]:
        with self._lock:
            return list(self._fills)

    def clear(self) -> None:
        with self._lock:
            self._by_client.clear()
            self._by_broker.clear()
            self._trade_ids.clear()
            self._fills.clear()

    # ---------------------------------------------------------- 回报处理

    def apply_update(self, update: OrderUpdate) -> Optional[Order]:
        with self._lock:
            order = None
            if update.client_id:
                order = self._by_client.get(update.client_id)
            if order is None and update.broker_order_id:
                order = self._by_broker.get(update.broker_order_id)
            status = map_status(update.status)
            if order is None:
                if not update.broker_order_id or not update.symbol or update.qty <= 0:
                    return None
                # 柜台侧手工单或别的客户端下的单：只读登记，不参与本地风控
                order = Order(
                    order_id="EXT-%s" % update.broker_order_id,
                    client_id=update.client_id or update.broker_order_id,
                    symbol=update.symbol, side=update.side, qty=update.qty or 0,
                    order_type="LIMIT", limit_price=update.price,
                    status=status, filled_qty=update.filled_qty,
                    avg_fill_price=update.avg_price,
                    created_at=update.updated_at, updated_at=update.updated_at,
                    broker_order_id=update.broker_order_id,
                    reason=update.reason,
                )
            else:
                if status != order.status and status not in ALLOWED.get(order.status, set()):
                    return order
                order = order.evolve(
                    status=status,
                    filled_qty=max(order.filled_qty, min(update.filled_qty, order.qty)),
                    avg_fill_price=update.avg_price or order.avg_fill_price,
                    updated_at=update.updated_at,
                    reason=update.reason or order.reason,
                    broker_order_id=update.broker_order_id or order.broker_order_id,
                )
            self._by_client[order.client_id] = order
            self._by_id[order.order_id] = order
            if order.broker_order_id:
                self._by_broker[order.broker_order_id] = order
            return order

    def apply_trade(self, trade: TradeUpdate) -> Optional[Fill]:
        if not trade.trade_id:
            return None
        with self._lock:
            if trade.trade_id in self._trade_ids:
                return None
            order = None
            if trade.client_id:
                order = self._by_client.get(trade.client_id)
            if order is None and trade.broker_order_id:
                order = self._by_broker.get(trade.broker_order_id)
            if order is None or trade.qty <= 0:
                return None
            self._trade_ids.add(trade.trade_id)
            fill = Fill(
                fill_id="BRK-%s" % trade.trade_id,
                order_id=order.order_id, symbol=trade.symbol or order.symbol,
                side=trade.side or order.side, qty=trade.qty,
                price=trade.price, dt=trade.dt or datetime.now(),
                commission=trade.fees,
                trade_id=trade.trade_id,
            )
            self._fills.append(fill)
            return fill

    def mark_unknown(self, reason: str = "连接断开，状态未知") -> List[Order]:
        """断线：把在场订单标为 UNKNOWN，返回变化的订单。"""
        with self._lock:
            changed: List[Order] = []
            for order in list(self._by_client.values()):
                if order.is_open:
                    updated = order.evolve(status=ST_UNKNOWN, reason=reason,
                                           updated_at=datetime.now())
                    self._by_client[order.client_id] = updated
                    self._by_id[order.order_id] = updated
                    if updated.broker_order_id:
                        self._by_broker[updated.broker_order_id] = updated
                    changed.append(updated)
            return changed

    def reconcile(self, remote: List[OrderUpdate]) -> List[str]:
        """与柜台委托列表对账，返回需要提示的差异。"""
        issues: List[str] = []
        remote_by_broker = {o.broker_order_id: o for o in remote if o.broker_order_id}
        for order in self.all():
            if not order.is_open or not order.broker_order_id:
                continue
            update = remote_by_broker.get(order.broker_order_id)
            if update is None:
                issues.append("本地委托 %s（柜台 %s）在柜台列表中不存在"
                              % (order.order_id, order.broker_order_id))
                continue
            if map_status(update.status) != order.status:
                self.apply_update(update)
                issues.append("委托 %s 状态已按柜台同步为 %s"
                              % (order.order_id, map_status(update.status)))
        return issues
