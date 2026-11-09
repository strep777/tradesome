"""实盘券商适配：把 Gateway 包成 OrderRouter 认识的 Broker。

OrderRouter 负责风控、幂等、T+1 校验与审计；这里只做三件事：

1. 把本地订单（含市价单）翻译成柜台能接受的委托（A 股实盘一律限价）；
2. 记住 ``order_id -> broker_order_id``，撤单时能找到柜台那张单；
3. 把网关异常翻译成 TradingError，让路由层生成可读的拒单原因。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..core.errors import GatewayError, TradingError
from ..core.money import floor_price, round_price
from ..core.types import (
    ORDER_MARKET, ST_SUBMITTED, Instrument, Order,
)
from ..execution.ports import Broker
from ..gateway.base import BaseGateway


class LiveBroker(Broker):
    """真实柜台 Broker 实现（通过 Gateway）。"""

    def __init__(self, gateway: BaseGateway, gateway_name: str = "qmt",
                 protect_pct: float = 0.01,
                 instrument_lookup: Optional[Callable[[str], Optional[Instrument]]] = None
                 ) -> None:
        self.gateway = gateway
        self.gateway_name = gateway_name or gateway.name
        self.protect_pct = max(0.001, min(0.05, float(protect_pct)))
        self._lookup = instrument_lookup
        self._lock = threading.RLock()
        self._broker_ids: Dict[str, str] = {}
        self._orders: Dict[str, Order] = {}

    # ---------------------------------------------------------- Broker 协议

    def submit(self, order: Order, price: float) -> Order:
        if not self.gateway.is_connected:
            raise TradingError("尚未连接柜台，不能下单")
        limit_price = self._limit_price(order, price)
        payload = {
            "client_id": order.client_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "price": limit_price,
            "order_type": "LIMIT",
        }
        try:
            broker_order_id = self.gateway.place_order(payload)
        except GatewayError as exc:
            raise TradingError(str(exc), hint=exc.hint) from exc
        placed = order.evolve(
            status=ST_SUBMITTED,
            order_type="LIMIT" if order.order_type == ORDER_MARKET else order.order_type,
            limit_price=limit_price,
            broker_order_id=broker_order_id,
            gateway=self.gateway_name,
            updated_at=datetime.now(),
        )
        with self._lock:
            self._broker_ids[order.order_id] = broker_order_id
            self._orders[order.order_id] = placed
        return placed

    def cancel(self, order_id: str) -> bool:
        with self._lock:
            broker_order_id = self._broker_ids.get(order_id, "")
            order = self._orders.get(order_id)
        if not broker_order_id:
            return False
        try:
            return bool(self.gateway.cancel(broker_order_id,
                                            order.symbol if order else ""))
        except GatewayError:
            return False

    def open_orders(self) -> List[Order]:
        with self._lock:
            return [o for o in self._orders.values() if o.is_open]

    # ---------------------------------------------------------- 辅助

    def remember(self, order: Order) -> None:
        """柜台回报到达后同步本地快照（供撤单查标的）。"""
        with self._lock:
            self._orders[order.order_id] = order
            if order.broker_order_id:
                self._broker_ids[order.order_id] = order.broker_order_id

    def broker_order_id(self, order_id: str) -> str:
        with self._lock:
            return self._broker_ids.get(order_id, "")

    def _limit_price(self, order: Order, price: float) -> float:
        """市价单转为保护价限价：买入上浮、卖出下浮，避免扫穿盘口。"""
        if order.order_type != ORDER_MARKET and order.limit_price > 0:
            return round_price(order.limit_price)
        base = float(price or order.limit_price or 0.0)
        if base <= 0:
            raise TradingError("没有可用价格，无法估算保护价")
        instrument = self._resolve_instrument(order.symbol)
        tick = instrument.tick_size if instrument is not None else 0.01
        pct = self.protect_pct
        if instrument is not None and instrument.price_limit_pct > 0:
            pct = min(pct, instrument.price_limit_pct)
        if order.side == "BUY":
            return round_price(base * (1.0 + pct), tick)
        return floor_price(base * (1.0 - pct), tick)

    def _resolve_instrument(self, symbol: str) -> Optional[Instrument]:
        if self._lookup is None:
            return None
        try:
            return self._lookup(symbol)
        except Exception:  # noqa: BLE001 - 估算保护价失败不应阻断下单
            return None
