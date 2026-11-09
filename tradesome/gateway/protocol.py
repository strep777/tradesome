"""实盘网关的消息契约与数据类（v1）。

网关实现（模拟柜台、迅投 QMT）把柜台回报翻译成这里的冻结数据类，再交给
``LiveBroker`` / ``OrderManager``。数据类与具体券商 SDK 无关，因此换柜台时
只需要换网关实现，交易域不用改。

报文格式（JSON 一行一个对象）保留给回放与调试：

* 请求  ``{"v":1,"id":"1","cmd":"order","params":{...}}``
* 响应  ``{"v":1,"id":"1","ok":true,"data":{...}}``
* 事件  ``{"v":1,"event":"order","data":{...}}``

进程内网关直接构造数据类，不经过 JSON；``parse_message`` 等函数用于录制
报文、离线回放与自定义适配器。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Tuple

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765

HEARTBEAT_INTERVAL = 3.0
HEARTBEAT_TIMEOUT = 10.0
REQUEST_TIMEOUT = 10.0

# 网关状态
GW_CONNECTING = "connecting"
GW_CONNECTED = "connected"
GW_DISCONNECTED = "disconnected"
GW_ERROR = "error"

EVENTS = ("status", "account", "position", "order", "trade", "tick", "log")
COMMANDS = (
    "hello", "ping", "connect", "disconnect", "subscribe",
    "order", "cancel", "cancel_all", "account", "positions", "orders", "trades",
)

# 错误码
E_AUTH = "E_AUTH"
E_PROTOCOL = "E_PROTOCOL"
E_TIMEOUT = "E_TIMEOUT"
E_DISCONNECTED = "E_DISCONNECTED"
E_GATEWAY = "E_GATEWAY"
E_REJECTED = "E_REJECTED"


@dataclass(frozen=True)
class AccountUpdate:
    """券商资金账户快照。"""

    cash: float = 0.0
    available: float = 0.0
    frozen_cash: float = 0.0
    market_value: float = 0.0
    equity: float = 0.0
    account_id: str = ""
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class PositionUpdate:
    """券商持仓快照。"""

    symbol: str
    qty: int = 0
    available_qty: int = 0
    avg_cost: float = 0.0
    last_price: float = 0.0
    market_value: float = 0.0


@dataclass(frozen=True)
class OrderUpdate:
    """委托状态回报。"""

    client_id: str = ""
    broker_order_id: str = ""
    symbol: str = ""
    side: str = "BUY"
    qty: int = 0
    filled_qty: int = 0
    price: float = 0.0
    avg_price: float = 0.0
    status: str = "SUBMITTED"
    reason: str = ""
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class TradeUpdate:
    """成交回报。"""

    trade_id: str = ""
    client_id: str = ""
    broker_order_id: str = ""
    symbol: str = ""
    side: str = "BUY"
    qty: int = 0
    price: float = 0.0
    fees: float = 0.0
    dt: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class TickUpdate:
    """最新价推送。"""

    symbol: str = ""
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    dt: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------- 编解码


def dumps(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(line: str) -> Dict[str, Any]:
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("消息必须是 JSON 对象")
    return data


def make_request(req_id: str, cmd: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": str(req_id), "cmd": cmd,
            "params": dict(params or {})}


def make_response(req_id: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": str(req_id), "ok": True,
            "data": dict(data or {})}


def make_error(req_id: str, code: str, message: str) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "id": str(req_id), "ok": False,
            "error": {"code": code, "message": message}}


def make_event(event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "event": event, "data": dict(data or {})}


def parse_message(line) -> Tuple[str, Dict[str, Any]]:
    """返回 (kind, payload)，kind ∈ request/response/event。

    既接受 JSON 文本行，也接受已经解析好的字典，便于服务端与测试复用。
    """
    data = line if isinstance(line, dict) else loads(line)
    if "event" in data:
        return "event", data
    if "cmd" in data:
        return "request", data
    if "id" in data:
        return "response", data
    raise ValueError("无法识别的消息类型")


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        text = value.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text[:26], fmt)
            except ValueError:
                continue
    return datetime.now()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def account_from_dict(data: Dict[str, Any]) -> AccountUpdate:
    return AccountUpdate(
        cash=_float(data.get("cash") or data.get("balance")),
        available=_float(data.get("available") or data.get("available_cash")),
        frozen_cash=_float(data.get("frozen_cash") or data.get("frozen")),
        market_value=_float(data.get("market_value") or data.get("market")),
        equity=_float(data.get("equity") or data.get("total")),
        account_id=str(data.get("account_id") or data.get("accountid") or ""),
        updated_at=_dt(data.get("updated_at")),
    )


def position_from_dict(data: Dict[str, Any]) -> PositionUpdate:
    qty = _int(data.get("qty") if data.get("qty") is not None else data.get("volume"))
    last = _float(data.get("last_price") or data.get("price"))
    return PositionUpdate(
        symbol=str(data.get("symbol") or ""),
        qty=qty,
        available_qty=_int(data.get("available_qty", data.get("available", qty))),
        avg_cost=_float(data.get("avg_cost") or data.get("avg_price")),
        last_price=last,
        market_value=_float(data.get("market_value")) or last * qty,
    )


def order_from_dict(data: Dict[str, Any]) -> OrderUpdate:
    return OrderUpdate(
        client_id=str(data.get("client_id") or ""),
        broker_order_id=str(data.get("broker_order_id") or data.get("order_id") or ""),
        symbol=str(data.get("symbol") or ""),
        side=str(data.get("side") or "BUY").upper(),
        qty=_int(data.get("qty") or data.get("volume")),
        filled_qty=_int(data.get("filled_qty") or data.get("traded")),
        price=_float(data.get("price")),
        avg_price=_float(data.get("avg_price") or data.get("avg_fill_price")),
        status=str(data.get("status") or "SUBMITTED").upper(),
        reason=str(data.get("reason") or data.get("message") or ""),
        updated_at=_dt(data.get("updated_at")),
    )


def trade_from_dict(data: Dict[str, Any]) -> TradeUpdate:
    return TradeUpdate(
        trade_id=str(data.get("trade_id") or data.get("tradeid") or ""),
        client_id=str(data.get("client_id") or ""),
        broker_order_id=str(data.get("broker_order_id") or data.get("order_id") or ""),
        symbol=str(data.get("symbol") or ""),
        side=str(data.get("side") or "BUY").upper(),
        qty=_int(data.get("qty") or data.get("volume")),
        price=_float(data.get("price")),
        fees=_float(data.get("fees")),
        dt=_dt(data.get("dt") or data.get("time")),
    )


def tick_from_dict(data: Dict[str, Any]) -> TickUpdate:
    return TickUpdate(
        symbol=str(data.get("symbol") or ""),
        last_price=_float(data.get("last_price") or data.get("last")),
        bid=_float(data.get("bid") or data.get("bid_price")),
        ask=_float(data.get("ask") or data.get("ask_price")),
        volume=_float(data.get("volume")),
        dt=_dt(data.get("dt") or data.get("time")),
    )
