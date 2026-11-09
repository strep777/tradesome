"""网关抽象：回测/模拟盘/实盘共用的柜台边界。

设计原则：

1. 本模块只认识 ``protocol`` 里的数据类，不 import tkinter/数据库；
2. 所有方法都可能被 UI 线程或后台线程调用，实现必须自己保证线程安全；
3. 事件通过 ``set_listener`` 注册的回调异步推送，回调里禁止做耗时操作。

接口刻意保持很小：连接、下单、撤单、查询、订阅。柜台差异全部由具体网关
吸收；``QmtGateway`` 在进程内调用随 QMT 客户端分发的 ``xtquant``，TradeSome
本体不链接任何 C++ 柜台库。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from .protocol import (
    AccountUpdate, GW_CONNECTED, OrderUpdate, PositionUpdate, TickUpdate,
)

logger = logging.getLogger("tradesome.gateway")

Listener = Callable[[str, Any], None]


class BaseGateway(ABC):
    """网关基类，统一监听器注册与异常隔离。"""

    name = "base"

    def __init__(self) -> None:
        self._listener: Optional[Listener] = None

    # ---------------------------------------------------------- 生命周期

    def set_listener(self, listener: Optional[Listener]) -> None:
        self._listener = listener

    def _emit(self, event: str, data: Any) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            listener(event, data)
        except Exception:  # 回调异常不能影响网关线程
            logger.exception("网关事件回调失败：%s", event)

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @property
    def status(self) -> str:
        return GW_CONNECTED if self.is_connected else "disconnected"

    @abstractmethod
    def start(self) -> None:
        """启动连接（异步），失败通过 status/error 事件汇报。"""

    @abstractmethod
    def stop(self) -> None:
        """断开并释放资源，可重复调用。"""

    # ---------------------------------------------------------- 交易

    @abstractmethod
    def place_order(self, order: Dict[str, Any]) -> str:
        """提交委托，返回柜台委托号。

        ``order`` 字段：client_id/symbol/side/qty/price/order_type。
        """

    @abstractmethod
    def cancel(self, broker_order_id: str, symbol: str = "") -> bool:
        ...

    @abstractmethod
    def cancel_all(self, symbol: str = "") -> int:
        ...

    # ---------------------------------------------------------- 查询

    @abstractmethod
    def query_account(self) -> AccountUpdate:
        ...

    @abstractmethod
    def query_positions(self) -> List[PositionUpdate]:
        ...

    @abstractmethod
    def query_orders(self) -> List[OrderUpdate]:
        ...

    # ---------------------------------------------------------- 行情

    def subscribe(self, symbols: List[str]) -> None:
        """订阅行情；不支持行情推送的柜台可以什么都不做。"""
        _ = symbols

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "connected": self.is_connected, "status": self.status}


class SimGatewayBase(BaseGateway):
    """给本地模拟柜台留的公共口子（测试里也用它注入成交）。"""

    def feed_tick(self, tick: TickUpdate) -> None:
        raise NotImplementedError

    def force_fill(self, broker_order_id: str, qty: Optional[int] = None,
                   price: Optional[float] = None) -> None:
        raise NotImplementedError
