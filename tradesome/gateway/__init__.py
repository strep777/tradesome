"""网关层：协议、抽象与两种实现（模拟柜台 / 迅投 QMT）。"""

from .base import BaseGateway, Listener
from .protocol import (
    AccountUpdate, OrderUpdate, PositionUpdate, TickUpdate, TradeUpdate,
    DEFAULT_HOST, DEFAULT_PORT, PROTOCOL_VERSION,
)
from .qmt import QmtGateway
from .sim import SimGateway

GATEWAY_KINDS = ("sim", "qmt")


def create_gateway(kind: str = "sim", **kwargs):
    """按配置创建网关；多余参数只传给对应的实现。"""
    kind = (kind or "sim").strip().lower()
    if kind == "sim":
        allowed = ("initial_cash", "fee", "auto_fill", "fill_ratio")
        return SimGateway(**{k: v for k, v in kwargs.items() if k in allowed})
    if kind == "qmt":
        allowed = ("path", "account_id", "account_type", "session_id",
                   "module", "connect_timeout")
        return QmtGateway(**{k: v for k, v in kwargs.items() if k in allowed})
    raise ValueError("未知网关类型：%s" % kind)


__all__ = [
    "BaseGateway", "Listener", "QmtGateway", "SimGateway", "GATEWAY_KINDS",
    "create_gateway", "AccountUpdate", "OrderUpdate", "PositionUpdate",
    "TickUpdate", "TradeUpdate", "PROTOCOL_VERSION", "DEFAULT_HOST", "DEFAULT_PORT",
]
