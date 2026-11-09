"""本地模拟柜台。

用途有两个：

1. 实盘界面里的「模拟柜台」模式，让人不连券商也能把整个实盘流程走通；
2. 测试里充当可编排的对手方（``force_fill`` 注入部分成交、``feed_tick`` 推价）。

它刻意做得像真实柜台：委托先进入 SUBMITTED，价格触及才成交；买入冻结资金；
T+1 当日买入不可卖；费用按 A 股规则实收。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.errors import GatewayError, TradingError
from ..core.money import round_money, round_price
from ..core.types import (
    ORDER_LIMIT, ORDER_MARKET, SIDE_BUY, SIDE_SELL, FeeProfile,
)
from .base import SimGatewayBase
from .protocol import (
    AccountUpdate, GW_CONNECTED, GW_DISCONNECTED, GW_ERROR,
    OrderUpdate, PositionUpdate, TickUpdate, TradeUpdate,
)

logger = logging.getLogger("tradesome.gateway.sim")


class SimGateway(SimGatewayBase):
    """进程内的模拟柜台。"""

    name = "sim"

    def __init__(self, initial_cash: float = 100000.0, fee: Optional[FeeProfile] = None,
                 auto_fill: bool = True, fill_ratio: float = 1.0) -> None:
        super().__init__()
        self.initial_cash = round_money(initial_cash)
        self.fee = fee or FeeProfile()
        self.auto_fill = bool(auto_fill)
        self.fill_ratio = max(0.0, min(1.0, float(fill_ratio)))
        self._lock = threading.RLock()
        self._connected = False
        self.cash = self.initial_cash
        self.frozen_cash = 0.0
        self._positions: Dict[str, Dict[str, float]] = {}
        self._last: Dict[str, float] = {}
        self._orders: Dict[str, OrderUpdate] = {}
        self._reserved: Dict[str, float] = {}
        self._today_buys: Dict[str, int] = {}
        self._seq = 0
        self._trade_seq = 0
        self._day = datetime.now().date()

    # ---------------------------------------------------------- 生命周期

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        with self._lock:
            self._connected = True
            self._day = datetime.now().date()
        self._emit("status", {"status": GW_CONNECTED, "gateway": self.name,
                              "message": "模拟柜台已连接"})
        self._push_account()
        for position in self.query_positions():
            self._emit("position", position)

    def stop(self) -> None:
        with self._lock:
            was = self._connected
            self._connected = False
        if was:
            self._emit("status", {"status": GW_DISCONNECTED, "gateway": self.name,
                                  "message": "模拟柜台已断开"})

    def disconnect(self) -> None:
        """模拟柜台没有网络，"断线"用于演练重连提示。"""
        self.stop()

    # ---------------------------------------------------------- 交易

    def place_order(self, order: Dict[str, Any]) -> str:
        symbol = str(order.get("symbol") or "")
        side = str(order.get("side") or SIDE_BUY).upper()
        qty = int(order.get("qty") or 0)
        price = float(order.get("price") or 0.0)
        order_type = str(order.get("order_type") or ORDER_LIMIT).upper()
        client_id = str(order.get("client_id") or "")
        if not symbol:
            raise GatewayError("委托缺少标的代码")
        if qty <= 0:
            raise GatewayError("委托数量必须为正")
        if side not in (SIDE_BUY, SIDE_SELL):
            raise GatewayError("委托方向非法：%s" % side)
        if order_type not in (ORDER_LIMIT, ORDER_MARKET):
            raise GatewayError("委托类型非法：%s" % order_type)
        if order_type == ORDER_LIMIT and price <= 0:
            raise GatewayError("限价委托必须给出价格")

        with self._lock:
            if not self._connected:
                raise GatewayError("模拟柜台未连接")
            self._roll_day()
            if order_type == ORDER_MARKET:
                price = self._last.get(symbol) or price
                if price <= 0:
                    raise TradingError("没有 %s 的最新价，无法市价委托" % symbol)
            price = round_price(price)
            if side == SIDE_BUY:
                need = round_money(price * qty + self._estimate_fees(side, price * qty))
                if need > self.cash + 1e-6:
                    raise TradingError("可用资金不足：需要 %.2f，可用 %.2f" % (need, self.cash))
                self.cash = round_money(self.cash - need)
                self.frozen_cash = round_money(self.frozen_cash + need)
            else:
                available = self._available_qty(symbol)
                if qty > available:
                    raise TradingError("可卖数量不足：%s 可卖 %d，委托 %d"
                                       % (symbol, available, qty))

            self._seq += 1
            broker_order_id = "SIM-%s-%04d" % (datetime.now().strftime("%H%M%S"), self._seq)
            update = OrderUpdate(
                client_id=client_id, broker_order_id=broker_order_id, symbol=symbol,
                side=side, qty=qty, filled_qty=0, price=price, avg_price=0.0,
                status="SUBMITTED", reason="", updated_at=datetime.now(),
            )
            self._orders[broker_order_id] = update
            if side == SIDE_BUY:
                self._reserved[broker_order_id] = round_money(
                    price * qty + self._estimate_fees(side, price * qty))
        self._emit("order", update)

        if self.auto_fill and self._crosses(update):
            fill_qty = int(qty * self.fill_ratio) if self.fill_ratio < 1.0 else qty
            fill_qty = max(0, min(qty, fill_qty))
            if fill_qty > 0:
                self._fill(broker_order_id, fill_qty, price)
        return broker_order_id

    def cancel(self, broker_order_id: str, symbol: str = "") -> bool:
        with self._lock:
            update = self._orders.get(broker_order_id)
            if update is None or not _is_open(update.status):
                return False
            if update.side == SIDE_BUY:
                reserved = self._reserved.pop(broker_order_id, 0.0)
                self.frozen_cash = round_money(max(0.0, self.frozen_cash - reserved))
                self.cash = round_money(self.cash + reserved)
            changed = update_with_status(update, "CANCELLED", "用户撤单")
            self._orders[broker_order_id] = changed
        self._emit("order", changed)
        self._push_account()
        return True

    def cancel_all(self, symbol: str = "") -> int:
        with self._lock:
            targets = [oid for oid, o in self._orders.items()
                       if _is_open(o.status) and (not symbol or o.symbol == symbol)]
        count = 0
        for oid in targets:
            if self.cancel(oid):
                count += 1
        return count

    def force_fill(self, broker_order_id: str, qty: Optional[int] = None,
                   price: Optional[float] = None) -> None:
        """注入成交（测试/演示用）。"""
        with self._lock:
            update = self._orders.get(broker_order_id)
            if update is None:
                raise GatewayError("不存在委托 %s" % broker_order_id)
            remaining = update.qty - update.filled_qty
            amount = remaining if qty is None else max(0, min(remaining, int(qty)))
            fill_price = round_price(price or update.price or self._last.get(update.symbol, 0))
            if fill_price <= 0:
                raise GatewayError("没有可成交的价格")
        if amount > 0:
            self._fill(broker_order_id, amount, fill_price)

    def _fill(self, broker_order_id: str, qty: int, price: float) -> None:
        with self._lock:
            update = self._orders.get(broker_order_id)
            if update is None or not _is_open(update.status):
                return
            last = self._last.get(update.symbol, 0.0)
            if last > 0:
                # 限价单以更优的现价成交，而不是机械地按委托价
                price = min(price, last) if update.side == SIDE_BUY else max(price, last)
            amount = price * qty
            fees = self._estimate_fees(update.side, amount)
            filled = update.filled_qty + qty
            if update.side == SIDE_BUY:
                reserved = self._reserved.get(broker_order_id, 0.0)
                total = round_money(amount + fees)
                if filled >= update.qty:
                    release = reserved
                else:
                    release = round_money(reserved * (qty / float(update.qty)))
                release = min(reserved, max(0.0, release))
                self._reserved[broker_order_id] = round_money(max(0.0, reserved - release))
                self.frozen_cash = round_money(max(0.0, self.frozen_cash - release))
                self.cash = round_money(self.cash + release - total)
                book = self._positions.setdefault(
                    update.symbol, {"qty": 0, "available": 0, "cost": 0.0})
                old_qty = int(book["qty"])
                new_qty = old_qty + qty
                book["cost"] = round_money(
                    (book["cost"] * old_qty + amount + fees) / new_qty) if new_qty else 0.0
                book["qty"] = new_qty
                self._today_buys[update.symbol] = self._today_buys.get(update.symbol, 0) + qty
                self._last[update.symbol] = price
            else:
                book = self._positions.get(update.symbol)
                if book is None or book["qty"] < qty:
                    raise TradingError("持仓不足，无法成交卖出")
                book["qty"] = int(book["qty"]) - qty
                book["available"] = max(0, int(book["available"]) - qty)
                self.cash = round_money(self.cash + amount - fees)
                self._last[update.symbol] = price
                if book["qty"] <= 0:
                    self._positions.pop(update.symbol, None)

            avg = ((update.avg_price * update.filled_qty) + amount) / filled if filled else 0.0
            status = "FILLED" if filled >= update.qty else "PARTIAL"
            changed = update_with_status(update, status, "", filled=filled, avg_price=avg)
            self._orders[broker_order_id] = changed

            self._trade_seq += 1
            trade = TradeUpdate(
                trade_id="SIMT-%s-%04d" % (datetime.now().strftime("%H%M%S"), self._trade_seq),
                client_id=update.client_id, broker_order_id=broker_order_id,
                symbol=update.symbol, side=update.side, qty=qty,
                price=round_price(price), fees=fees, dt=datetime.now(),
            )
        self._emit("trade", trade)
        self._emit("order", changed)
        self._push_account()
        self._push_position(update.symbol)

    # ---------------------------------------------------------- 查询

    def query_account(self) -> AccountUpdate:
        with self._lock:
            market_value = sum(book["qty"] * self._last.get(sym, book["cost"])
                               for sym, book in self._positions.items())
            equity = round_money(self.cash + self.frozen_cash + market_value)
            return AccountUpdate(
                cash=self.cash, available=self.cash, frozen_cash=self.frozen_cash,
                market_value=round_money(market_value), equity=equity,
                account_id="SIM", updated_at=datetime.now(),
            )

    def query_positions(self) -> List[PositionUpdate]:
        with self._lock:
            result = []
            for symbol, book in self._positions.items():
                last = self._last.get(symbol, book["cost"])
                result.append(PositionUpdate(
                    symbol=symbol, qty=int(book["qty"]),
                    available_qty=int(book["available"]),
                    avg_cost=round_money(book["cost"]),
                    last_price=round_price(last),
                    market_value=round_money(last * book["qty"]),
                ))
            return result

    def query_orders(self) -> List[OrderUpdate]:
        with self._lock:
            return list(self._orders.values())

    # ---------------------------------------------------------- 行情

    def feed_tick(self, tick: TickUpdate) -> None:
        if not tick.symbol or tick.last_price <= 0:
            return
        with self._lock:
            self._last[tick.symbol] = float(tick.last_price)
            pending = [o for o in self._orders.values()
                       if _is_open(o.status) and o.symbol == tick.symbol]
        self._emit("tick", tick)
        for update in pending:
            if self._crosses(update):
                self._fill(update.broker_order_id, update.qty - update.filled_qty,
                           update.price or tick.last_price)
        self._push_account()
        self._push_position(tick.symbol)

    def reset(self, initial_cash: Optional[float] = None) -> None:
        with self._lock:
            self.initial_cash = round_money(initial_cash or self.initial_cash)
            self.cash = self.initial_cash
            self.frozen_cash = 0.0
            self._positions.clear()
            self._last.clear()
            self._orders.clear()
            self._reserved.clear()
            self._today_buys.clear()

    # ---------------------------------------------------------- 内部

    def _crosses(self, update: OrderUpdate) -> bool:
        last = self._last.get(update.symbol, 0.0)
        if last <= 0:
            # 没有行情时，限价单按委托价立即成交（演示模式）
            return update.status == "SUBMITTED" and update.price > 0
        if update.side == SIDE_BUY:
            return update.price >= last
        return update.price <= last

    def _available_qty(self, symbol: str) -> int:
        book = self._positions.get(symbol)
        if book is None:
            return 0
        return max(0, int(book["available"]))

    def _roll_day(self) -> None:
        today = datetime.now().date()
        if today == self._day:
            return
        self._day = today
        for book in self._positions.values():
            book["available"] = int(book["qty"])
        self._today_buys.clear()

    def _estimate_fees(self, side: str, amount: float) -> float:
        commission = max(self.fee.min_commission, amount * self.fee.commission_rate)
        transfer = amount * self.fee.transfer_fee_rate
        stamp = amount * self.fee.stamp_tax_rate if side == SIDE_SELL else 0.0
        return round_money(commission + transfer + stamp)

    def _push_account(self) -> None:
        self._emit("account", self.query_account())

    def _push_position(self, symbol: str) -> None:
        for position in self.query_positions():
            if position.symbol == symbol:
                self._emit("position", position)
                return
        self._emit("position", PositionUpdate(symbol=symbol))


def _is_open(status: str) -> bool:
    return status in ("PENDING", "SUBMITTED", "PARTIAL")


def update_with_status(base: OrderUpdate, status: str, reason: str = "",
                       filled: Optional[int] = None,
                       avg_price: Optional[float] = None) -> OrderUpdate:
    """生成新的委托快照（OrderUpdate 是冻结对象）。"""
    from dataclasses import replace

    return replace(
        base, status=status, reason=reason,
        filled_qty=base.filled_qty if filled is None else int(filled),
        avg_price=base.avg_price if avg_price is None else round_price(avg_price),
        updated_at=datetime.now(),
    )


def _error_status(message: str) -> Dict[str, str]:
    return {"status": GW_ERROR, "gateway": "sim", "message": message}
