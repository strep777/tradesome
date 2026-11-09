"""订单路由：所有订单的唯一入口。

顺序固定：格式/交易规则 → 幂等 → 风控 → 券商 → 审计。
任何一步失败都返回状态为 REJECTED 的订单（不抛异常），策略据此决定后续动作。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..core.events import ORDER_CANCELLED, ORDER_REJECTED, ORDER_SUBMITTED
from ..core.ids import new_order_id
from ..core.logging import get_logger
from ..core.instruments import validate_qty
from ..core.money import round_money
from ..core.types import (
    ORDER_LIMIT, ST_REJECTED, ST_SUBMITTED, AccountSnapshot, Instrument, Order,
    OrderRequest,
)
from ..portfolio.account import Account
from ..risk.manager import RiskManager

LOGGER = get_logger("execution.router")


class OrderRouter:
    def __init__(self, account: Account, broker, risk: RiskManager,
                 instruments, audit=None, bus=None,
                 price_lookup: Optional[Callable[[str], Optional[float]]] = None) -> None:
        self.account = account
        self.broker = broker
        self.risk = risk
        self.instruments = instruments
        self.audit = audit
        self.bus = bus
        self.price_lookup = price_lookup
        self._lock = threading.RLock()
        self._by_client: Dict[str, Order] = {}
        self.submitted = 0
        self.rejected = 0
        self.cancelled = 0

    # ---------------------------------------------------------- 下单

    def submit(self, request: OrderRequest, strategy_id: str = "",
               session_id: str = "", price: Optional[float] = None,
               is_open_session: bool = True,
               now: Optional[datetime] = None) -> Order:
        moment = now or datetime.now()
        with self._lock:
            instrument = self.instruments.get(request.symbol)
            if instrument is None:
                return self._reject(request, strategy_id, session_id, moment,
                                    "E_UNKNOWN_SYMBOL", "未知标的：%s" % request.symbol)

            ok, message = validate_qty(instrument, request.qty)
            if not ok:
                return self._reject(request, strategy_id, session_id, moment, "E_LOT", message)

            if request.order_type == ORDER_LIMIT and request.limit_price <= 0:
                return self._reject(request, strategy_id, session_id, moment,
                                    "E_PRICE", "限价单价格必须大于 0")

            client_id = request.client_id or ("%s:%s:%s:%s" % (
                strategy_id or "manual", request.symbol, request.side,
                moment.strftime("%Y%m%d%H%M%S")))
            existing = self._by_client.get(client_id)
            if existing is not None:
                LOGGER.info("重复订单已忽略：%s", client_id)
                return existing

            current = float(price) if price else self._price_of(request.symbol)
            if not current or current <= 0:
                current = float(request.limit_price or 0)
            if current <= 0:
                return self._reject(request, strategy_id, session_id, moment,
                                    "E_NO_QUOTE", "没有可用行情，无法下单")

            if request.side == "SELL":
                available = self.account.available_qty(request.symbol)
                if request.qty > available:
                    return self._reject(
                        request, strategy_id, session_id, moment, "E_T1",
                        "可卖数量不足：需要 %d 股，T+1 可用 %d 股"
                        % (request.qty, available))

            snapshot = self.account.snapshot(moment)
            decision = self.risk.pre_trade(request, current, snapshot, instrument,
                                           now=moment, is_open_session=is_open_session)
            if decision.rejected:
                order = self._reject(request, strategy_id, session_id, moment,
                                     "E_RISK", decision.message,
                                     rule_id=decision.rule_id)
                return order

            order = Order(
                order_id=new_order_id(moment), client_id=client_id,
                symbol=request.symbol, side=request.side, qty=request.qty,
                order_type=request.order_type,
                limit_price=float(request.limit_price or 0.0),
                status=ST_SUBMITTED, created_at=moment, updated_at=moment,
                strategy_id=strategy_id, session_id=session_id,
                reason=request.reason,
            )
            self._by_client[client_id] = order
            self.submitted += 1
            try:
                placed = self.broker.submit(order, current)
            except Exception as exc:  # noqa: BLE001 - 券商异常统一转拒绝
                LOGGER.exception("券商拒单")
                return self._reject(request, strategy_id, session_id, moment,
                                    "E_BROKER", "券商异常：%s" % exc)
            self._by_client[client_id] = placed
            self._publish(ORDER_SUBMITTED, placed, "订单已提交")
            self._audit("order", "submit", "提交订单 %s %s %s x%d"
                        % (placed.order_id, placed.side, placed.symbol, placed.qty))
            return placed

    def cancel(self, order_id: str) -> bool:
        with self._lock:
            ok = self.broker.cancel(order_id)
            if ok:
                self.cancelled += 1
                self._publish(ORDER_CANCELLED, None, "订单已撤销 %s" % order_id,
                              order_id=order_id)
                self._audit("order", "cancel", "撤销订单 %s" % order_id)
            return ok

    # ---------------------------------------------------------- 内部

    def _price_of(self, symbol: str) -> Optional[float]:
        if self.price_lookup is not None:
            try:
                return self.price_lookup(symbol)
            except Exception:  # noqa: BLE001 - 行情缺失不该导致下单崩溃
                return None
        position = self.account.position_of(symbol)
        return position.last_price if position is not None else None

    def _reject(self, request: OrderRequest, strategy_id: str, session_id: str,
                moment: datetime, code: str, message: str,
                rule_id: str = "") -> Order:
        self.rejected += 1
        order = Order(
            order_id=new_order_id(moment), client_id=request.client_id or "",
            symbol=request.symbol, side=request.side, qty=request.qty,
            order_type=request.order_type, limit_price=float(request.limit_price or 0.0),
            status=ST_REJECTED, created_at=moment, updated_at=moment,
            strategy_id=strategy_id, session_id=session_id,
            reason="%s：%s" % (code, message),
        )
        LOGGER.warning("订单被拒 %s %s：%s", request.side, request.symbol, message)
        self._publish(ORDER_REJECTED, order, message, code=code, rule_id=rule_id)
        self._audit("order", "reject", "拒单 %s %s %s：%s"
                    % (order.order_id, request.side, request.symbol, message),
                    severity="WARNING")
        return order

    def _publish(self, topic: str, order: Optional[Order], message: str, **extra) -> None:
        if self.bus is None:
            return
        payload = {"message": message}
        if order is not None:
            payload["order"] = order
        payload.update(extra)
        self.bus.emit(topic, **payload)

    def _audit(self, category: str, action: str, detail: str,
               severity: str = "INFO") -> None:
        if self.audit is None:
            return
        try:
            self.audit.write(category=category, action=action, detail=detail,
                             severity=severity)
        except Exception:  # noqa: BLE001 - 审计失败不影响交易主流程
            LOGGER.exception("写审计日志失败")

    def stats(self) -> Dict[str, int]:
        return {"submitted": self.submitted, "rejected": self.rejected,
                "cancelled": self.cancelled}
