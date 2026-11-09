"""迅投 QMT / miniQMT 原生网关。

这是 TradeSome 自己实现的柜台适配器，**不依赖 vnpy**：只使用券商 QMT 客户端
自带的 ``xtquant`` Python 包（随 miniQMT 安装，不是 pip 包），在进程内直接
连接本机客户端，完成报单、撤单、账户/持仓/委托/成交查询与回报推送。

设计要点（参考 vnpy 的 Gateway 分层，但代码完全独立）：

1. 只依赖 ``core`` 与本包的 ``protocol`` 数据类；
2. 回调在 xtquant 自己的线程里到达，统一翻译成 ``protocol`` 事件；
3. 所有对外方法线程安全；``xtquant`` 延迟导入，未安装时给出中文提示；
4. 柜台账号与密码由 miniQMT 客户端自己管理，TradeSome 只保存
   ``userdata_mini`` 路径与资金账号，不接触券商密码。

行情：QMT 的下单不依赖订阅；本网关只做交易。行情仍由本地日线 + 手工刷新
的最新价提供，若需要实时行情可后续接 ``xtquant.xtdata``。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.errors import GatewayError, GatewayTimeout
from .base import BaseGateway
from .protocol import (
    AccountUpdate, GW_CONNECTED, GW_CONNECTING, GW_DISCONNECTED, GW_ERROR,
    OrderUpdate, PositionUpdate, TradeUpdate,
)

logger = logging.getLogger("tradesome.gateway.qmt")

# 委托类型（xtconstant 的数值）
_ORDER_BUY = 23
_ORDER_SELL = 24
_ORDER_MARGIN_BUY = 27
_ORDER_SHORT_SELL = 28
_BUY_TYPES = {_ORDER_BUY, _ORDER_MARGIN_BUY}
_SELL_TYPES = {_ORDER_SELL, _ORDER_SHORT_SELL}

# 委托状态（xtconstant 的数值）
_STATUS_MAP = {
    48: "SUBMITTED",   # 未报
    49: "SUBMITTED",   # 待报
    50: "SUBMITTED",   # 已报
    51: "SUBMITTED",   # 已报待撤
    52: "PARTIAL",     # 部成待撤
    53: "CANCELLED",   # 部撤
    54: "CANCELLED",   # 已撤
    55: "PARTIAL",     # 部成
    56: "FILLED",      # 已成
    57: "REJECTED",    # 废单
    255: "UNKNOWN",
}

# 账号状态（xtconstant.ACCOUNT_STATUS_*）
_ACCOUNT_OK = 0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dt(value: Any = None) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.now()


class QmtGateway(BaseGateway):
    """迅投 QMT / miniQMT 原生柜台网关。"""

    name = "qmt"

    def __init__(self, path: str, account_id: str, account_type: str = "STOCK",
                 session_id: Optional[int] = None, module: Any = None,
                 connect_timeout: float = 10.0) -> None:
        super().__init__()
        self.path = str(path or "").strip()
        self.account_id = str(account_id or "").strip()
        self.account_type = str(account_type or "STOCK").strip().upper()
        self.session_id = int(session_id or (int(time.time()) % 2_000_000_000))
        self.connect_timeout = float(connect_timeout)
        self._module = module
        self._trader: Any = None
        self._account: Any = None
        self._callback: Any = None
        self._connected = False
        self._closing = False
        self._lock = threading.RLock()
        self._client_ids: Dict[str, str] = {}
        self._last_error = ""
        self._trades: Dict[str, TradeUpdate] = {}

    # ---------------------------------------------------------- 环境

    @staticmethod
    def available() -> bool:
        """本机是否安装了 xtquant（随 QMT/miniQMT 客户端提供）。"""
        try:
            import importlib.util
            return importlib.util.find_spec("xtquant") is not None
        except Exception:  # noqa: BLE001 - 探测失败按不可用处理
            return False

    def _load_module(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            from xtquant import xtconstant
            from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
            from xtquant.xttype import StockAccount
        except Exception as exc:  # noqa: BLE001
            raise GatewayError(
                "未找到迅投 xtquant：请先安装并登录 QMT/miniQMT 客户端，"
                "然后确认其 Python 包在解释器路径中（无需安装 vnpy）。"
            ) from exc
        self._module = {
            "xtconstant": xtconstant,
            "XtQuantTrader": XtQuantTrader,
            "XtQuantTraderCallback": XtQuantTraderCallback,
            "StockAccount": StockAccount,
        }
        return self._module

    def _constant(self, name: str, default: int) -> int:
        module = self._load_module()
        constants = module.get("xtconstant") if isinstance(module, dict) else getattr(
            module, "xtconstant", None)
        return int(getattr(constants, name, default) or default)

    # ---------------------------------------------------------- 生命周期

    @property
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> str:  # type: ignore[override]
        if self._connected:
            return GW_CONNECTED
        if self._last_error:
            return GW_ERROR
        return GW_DISCONNECTED if self._trader is not None else "idle"

    def start(self) -> None:
        if self._connected:
            return
        if not self.path:
            raise GatewayError("请先填写 miniQMT 的 userdata_mini 目录")
        if not self.account_id:
            raise GatewayError("请先填写资金账号")
        self._closing = False
        self._last_error = ""
        self._emit("status", {"status": GW_CONNECTING})
        module = self._load_module()
        try:
            trader_cls = module["XtQuantTrader"] if isinstance(module, dict) else module.XtQuantTrader
            callback_base = (module["XtQuantTraderCallback"]
                            if isinstance(module, dict) else module.XtQuantTraderCallback)
            account_cls = module["StockAccount"] if isinstance(module, dict) else module.StockAccount
            trader = trader_cls(self.path, self.session_id)
            callback = self._make_callback(callback_base)
            trader.register_callback(callback)
            trader.start()
            result = trader.connect()
            if int(result) != 0:
                raise GatewayError(
                    "连接 miniQMT 失败（返回 %s）：请确认客户端已启动并登录，"
                    "且 userdata_mini 路径正确" % result)
            account = account_cls(self.account_id, self.account_type)
            subscribe_result = trader.subscribe(account)
            if int(subscribe_result) != 0:
                raise GatewayError(
                    "订阅资金账号失败（返回 %s）：请确认账号类型与资金账号" % subscribe_result)
        except Exception:
            self._last_error = "连接 QMT 失败"
            self._emit("status", {"status": GW_ERROR, "error": self._last_error})
            raise
        with self._lock:
            self._trader = trader
            self._account = account
            self._callback = callback
            self._connected = True
        self._emit("status", {"status": GW_CONNECTED})
        self._emit_snapshot()

    def _make_callback(self, base: Any) -> Any:
        gateway = self

        def on_disconnected(_self: Any) -> None:
            gateway._on_disconnected()

        def on_account_status(_self: Any, status: Any) -> None:
            gateway._on_account_status(status)

        def on_stock_order(_self: Any, order: Any) -> None:
            gateway._on_order(order)

        def on_stock_trade(_self: Any, trade: Any) -> None:
            gateway._on_trade(trade)

        def on_order_error(_self: Any, error: Any) -> None:
            gateway._on_order_error(error)

        def on_cancel_error(_self: Any, error: Any) -> None:
            gateway._on_cancel_error(error)

        return type("TradeSomeQmtCallback", (base,), {
            "on_disconnected": on_disconnected,
            "on_account_status": on_account_status,
            "on_stock_order": on_stock_order,
            "on_stock_trade": on_stock_trade,
            "on_order_error": on_order_error,
            "on_cancel_error": on_cancel_error,
        })()

    def _emit_snapshot(self) -> None:
        try:
            self._emit("account", self.query_account())
        except Exception:  # noqa: BLE001 - 快照失败不阻断连接
            logger.warning("连接后查询资产失败", exc_info=True)
        try:
            for position in self.query_positions():
                self._emit("position", position)
        except Exception:  # noqa: BLE001
            logger.warning("连接后查询持仓失败", exc_info=True)

    def stop(self) -> None:
        self._closing = True
        trader = self._trader
        self._connected = False
        if trader is not None:
            try:
                if self._account is not None:
                    trader.unsubscribe(self._account)
            except Exception:  # noqa: BLE001
                logger.debug("反订阅失败", exc_info=True)
            try:
                trader.stop()
            except Exception:  # noqa: BLE001
                logger.exception("停止 QMT 交易线程失败")
        with self._lock:
            self._trader = None
            self._account = None
        self._emit("status", {"status": GW_DISCONNECTED, "reason": "用户停止"})

    # ---------------------------------------------------------- 回报

    def _on_disconnected(self) -> None:
        if self._closing:
            return
        self._connected = False
        self._last_error = "与 miniQMT 的连接已断开"
        self._emit("status", {"status": GW_DISCONNECTED,
                              "error": self._last_error})

    def _on_account_status(self, status: Any) -> None:
        code = _as_int(getattr(status, "status", -1), -1)
        account = str(getattr(status, "account_id", "") or "")
        if code == _ACCOUNT_OK:
            self._emit("log", {"level": "INFO", "message": "资金账号 %s 状态正常" % account})
            return
        self._last_error = "资金账号状态异常（%s）" % code
        self._emit("status", {"status": GW_ERROR, "error": self._last_error})

    def _client_id_of(self, order: Any, broker_id: str) -> str:
        if broker_id and broker_id in self._client_ids:
            return self._client_ids[broker_id]
        remark = str(getattr(order, "order_remark", "") or "")
        return remark

    def _on_order(self, order: Any) -> None:
        update = self._order_update(order)
        if update is not None:
            self._emit("order", update)

    def _on_trade(self, trade: Any) -> None:
        update = self._trade_update(trade)
        if update is None:
            return
        self._trades[update.trade_id] = update
        self._emit("trade", update)

    def _on_order_error(self, error: Any) -> None:
        broker_id = str(_as_int(getattr(error, "order_id", 0), 0))
        remark = str(getattr(error, "order_remark", "") or "")
        client_id = self._client_ids.get(broker_id, remark)
        reason = str(getattr(error, "error_msg", "") or "柜台拒单")
        self._emit("order", OrderUpdate(
            client_id=client_id, broker_order_id=broker_id, symbol="",
            status="REJECTED", reason=reason, updated_at=datetime.now()))

    def _on_cancel_error(self, error: Any) -> None:
        broker_id = str(_as_int(getattr(error, "order_id", 0), 0))
        reason = str(getattr(error, "error_msg", "") or "撤单失败")
        self._emit("log", {"level": "WARNING",
                           "message": "撤单失败 %s：%s" % (broker_id, reason)})

    # ---------------------------------------------------------- 交易

    def _trader_or_raise(self) -> Any:
        if not self._connected or self._trader is None:
            raise GatewayError("QMT 尚未连接，请先连接后再下单")
        return self._trader

    def place_order(self, order: Dict[str, Any]) -> str:
        trader = self._trader_or_raise()
        side = str(order.get("side", "BUY")).upper()
        order_type = _ORDER_SELL if side == "SELL" else _ORDER_BUY
        qty = _as_int(order.get("qty", 0))
        price = _as_float(order.get("price", 0.0))
        if qty <= 0 or price <= 0:
            raise GatewayError("委托数量与价格必须大于 0")
        client_id = str(order.get("client_id", "") or "")
        remark = client_id[:24] or "TradeSome"
        price_type = self._constant("FIX_PRICE", 11)
        try:
            broker_id = trader.order_stock(
                self._account, str(order.get("symbol", "")), order_type, qty,
                price_type, price, str(order.get("strategy_name", "TradeSome")),
                remark)
        except Exception as exc:  # noqa: BLE001
            raise GatewayError("QMT 下单异常：%s" % exc) from exc
        broker_id = str(broker_id)
        if _as_int(broker_id, 0) <= 0:
            raise GatewayError("QMT 拒绝委托（返回 %s）" % broker_id)
        with self._lock:
            self._client_ids[broker_id] = client_id
        return broker_id

    def cancel(self, broker_order_id: str, symbol: str = "") -> bool:
        trader = self._trader_or_raise()
        try:
            result = trader.cancel_order_stock(self._account, _as_int(broker_order_id))
        except Exception:  # noqa: BLE001
            logger.warning("撤单异常 %s", broker_order_id, exc_info=True)
            return False
        return int(result) == 0

    def cancel_all(self, symbol: str = "") -> int:
        count = 0
        try:
            orders = self.query_orders()
        except Exception:  # noqa: BLE001
            return 0
        for order in orders:
            if order.status in ("FILLED", "CANCELLED", "REJECTED"):
                continue
            if symbol and order.symbol != symbol:
                continue
            if self.cancel(order.broker_order_id, order.symbol):
                count += 1
        return count

    # ---------------------------------------------------------- 查询

    def query_account(self) -> AccountUpdate:
        trader = self._trader_or_raise()
        try:
            asset = trader.query_stock_asset(self._account)
        except Exception as exc:  # noqa: BLE001
            raise GatewayTimeout("查询 QMT 资产失败：%s" % exc) from exc
        if asset is None:
            raise GatewayTimeout("查询 QMT 资产返回空，请稍后重试")
        return AccountUpdate(
            cash=_as_float(getattr(asset, "cash", 0.0)),
            available=_as_float(getattr(asset, "cash", 0.0)),
            frozen_cash=_as_float(getattr(asset, "frozen_cash", 0.0)),
            market_value=_as_float(getattr(asset, "market_value", 0.0)),
            equity=_as_float(getattr(asset, "total_asset", 0.0)),
            account_id=str(getattr(asset, "account_id", self.account_id) or self.account_id),
            updated_at=datetime.now())

    def query_positions(self) -> List[PositionUpdate]:
        trader = self._trader_or_raise()
        try:
            rows = trader.query_stock_positions(self._account) or []
        except Exception as exc:  # noqa: BLE001
            raise GatewayTimeout("查询 QMT 持仓失败：%s" % exc) from exc
        result: List[PositionUpdate] = []
        for row in rows:
            qty = _as_int(getattr(row, "volume", 0))
            if qty <= 0:
                continue
            market_value = _as_float(getattr(row, "market_value", 0.0))
            avg_cost = _as_float(getattr(row, "avg_price", 0.0)) or _as_float(
                getattr(row, "open_price", 0.0))
            last_price = market_value / qty if qty and market_value else avg_cost
            result.append(PositionUpdate(
                symbol=str(getattr(row, "stock_code", "")),
                qty=qty,
                available_qty=_as_int(getattr(row, "can_use_volume", qty)),
                avg_cost=avg_cost,
                last_price=last_price,
                market_value=market_value,
            ))
        return result

    def query_orders(self) -> List[OrderUpdate]:
        trader = self._trader_or_raise()
        try:
            rows = trader.query_stock_orders(self._account, False) or []
        except Exception as exc:  # noqa: BLE001
            raise GatewayTimeout("查询 QMT 委托失败：%s" % exc) from exc
        result: List[OrderUpdate] = []
        for row in rows:
            update = self._order_update(row)
            if update is not None:
                result.append(update)
        return result

    def query_trades(self) -> List[TradeUpdate]:
        trader = self._trader_or_raise()
        try:
            rows = trader.query_stock_trades(self._account) or []
        except Exception as exc:  # noqa: BLE001
            raise GatewayTimeout("查询 QMT 成交失败：%s" % exc) from exc
        result = []
        for row in rows:
            update = self._trade_update(row)
            if update is not None:
                result.append(update)
        return result

    # ---------------------------------------------------------- 翻译

    def _order_update(self, order: Any) -> Optional[OrderUpdate]:
        broker_id = str(_as_int(getattr(order, "order_id", 0)))
        if not broker_id or broker_id == "0":
            return None
        raw_type = _as_int(getattr(order, "order_type", _ORDER_BUY))
        side = "SELL" if raw_type in _SELL_TYPES else "BUY"
        raw_status = _as_int(getattr(order, "order_status", 255), 255)
        status = _STATUS_MAP.get(raw_status, "UNKNOWN")
        return OrderUpdate(
            client_id=self._client_id_of(order, broker_id),
            broker_order_id=broker_id,
            symbol=str(getattr(order, "stock_code", "")),
            side=side,
            qty=_as_int(getattr(order, "order_volume", 0)),
            filled_qty=_as_int(getattr(order, "traded_volume", 0)),
            price=_as_float(getattr(order, "price", 0.0)),
            avg_price=_as_float(getattr(order, "traded_price", 0.0)),
            status=status,
            reason=str(getattr(order, "status_msg", "") or ""),
            updated_at=datetime.now())

    def _trade_update(self, trade: Any) -> Optional[TradeUpdate]:
        trade_id = str(getattr(trade, "traded_id", "") or "")
        if not trade_id:
            return None
        broker_id = str(_as_int(getattr(trade, "order_id", 0)))
        raw_type = _as_int(getattr(trade, "order_type", _ORDER_BUY))
        side = "SELL" if raw_type in _SELL_TYPES else "BUY"
        return TradeUpdate(
            trade_id=trade_id,
            client_id=self._client_id_of(trade, broker_id),
            broker_order_id=broker_id,
            symbol=str(getattr(trade, "stock_code", "")),
            side=side,
            qty=_as_int(getattr(trade, "traded_volume", 0)),
            price=_as_float(getattr(trade, "traded_price", 0.0)),
            fees=0.0,
            dt=_dt(),
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "connected": self._connected,
            "status": self.status(),
            "path": self.path,
            "account": self.account_id,
            "account_type": self.account_type,
            "error": self._last_error,
        }
