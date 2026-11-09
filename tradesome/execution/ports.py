"""执行层端口：Broker 抽象与订单请求。"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from ..core.types import Order, OrderRequest

__all__ = ["Broker", "OrderRequest"]


@runtime_checkable
class Broker(Protocol):
    """券商接口。内置只有模拟券商；接真实券商时实现这三个方法即可。"""

    name: str

    def submit(self, order: Order, price: float) -> Order:
        ...

    def cancel(self, order_id: str) -> bool:
        ...

    def open_orders(self) -> List[Order]:
        ...
