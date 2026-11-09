"""账户与持仓：现金、冻结、T+1 可用数量、盯市与快照。

回测与模拟盘共用这一个实现，保证"回测怎么算，实盘就怎么算"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional

from ..core.errors import ExecutionError, ValidationError
from ..core.money import round_money
from ..core.types import (
    SIDE_BUY, SIDE_SELL, AccountSnapshot, Fill, Position,
)


class Account:
    def __init__(self, initial_cash: float = 100000.0, t_plus: int = 1) -> None:
        if initial_cash <= 0:
            raise ValidationError("初始资金必须大于 0")
        self.initial_cash = round_money(initial_cash)
        self.cash = self.initial_cash
        self.frozen_cash = 0.0
        self.t_plus = max(0, int(t_plus))
        self.positions: Dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.turnover = 0.0
        self.peak_equity = self.initial_cash
        self.day_start_equity = self.initial_cash
        self.last_dt: Optional[datetime] = None
        self._last_prices: Dict[str, float] = {}
        self._equity_override: Optional[float] = None
        self._reservations: Dict[str, float] = {}
        self._today_buys: Dict[str, int] = {}

    # ---------------------------------------------------------- 查询

    def position_of(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    @property
    def position_list(self) -> List[Position]:
        return list(self.positions.values())

    def available_qty(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        if position is None:
            return 0
        return position.available_qty

    @property
    def position_value(self) -> float:
        return round_money(sum(p.market_value for p in self.positions.values()))

    @property
    def equity(self) -> float:
        if self._equity_override is not None:
            return round_money(self._equity_override)
        return round_money(self.cash + self.frozen_cash + self.position_value)

    @property
    def gross_exposure(self) -> float:
        total = self.equity
        return (self.position_value / total) if total > 0 else 0.0

    @property
    def buying_power(self) -> float:
        return round_money(self.cash)

    # ---------------------------------------------------------- 冻结

    def freeze(self, order_id: str, amount: float) -> bool:
        amount = round_money(max(0.0, amount))
        if amount > self.cash + 1e-6:
            return False
        self.cash = round_money(self.cash - amount)
        self.frozen_cash = round_money(self.frozen_cash + amount)
        self._reservations[order_id] = self._reservations.get(order_id, 0.0) + amount
        return True

    def release(self, order_id: str) -> float:
        amount = self._reservations.pop(order_id, 0.0)
        if amount <= 0:
            return 0.0
        amount = min(amount, self.frozen_cash)
        self.frozen_cash = round_money(self.frozen_cash - amount)
        self.cash = round_money(self.cash + amount)
        return amount

    def frozen_for(self, order_id: str) -> float:
        return self._reservations.get(order_id, 0.0)

    # ---------------------------------------------------------- 成交

    def apply_fill(self, fill: Fill) -> float:
        """应用成交，返回本次实现盈亏（卖出）。"""
        symbol = fill.symbol
        fees = fill.fees
        realized = 0.0
        if fill.side == SIDE_BUY:
            total = round_money(fill.gross_amount + fees)
            reserved = self._reservations.pop(fill.order_id, 0.0)
            reserved = min(reserved, total)
            if reserved:
                self.frozen_cash = round_money(self.frozen_cash - reserved)
                total -= reserved
            if total > self.cash + 1e-6:
                raise ExecutionError("现金不足：需要 %.2f，可用 %.2f" % (total, self.cash))
            self.cash = round_money(self.cash - total)
            position = self.positions.get(symbol)
            if position is None:
                # 先建空持仓，下面的合并逻辑统一处理数量与成本
                position = Position(symbol=symbol, qty=0,
                                    available_qty=0, avg_cost=0.0,
                                    last_price=fill.price, opened_at=fill.dt)
            old_cost = position.avg_cost * position.qty
            new_qty = position.qty + fill.qty
            cost_per_share = (old_cost + fill.gross_amount + fees) / new_qty
            self.positions[symbol] = position.evolve(
                qty=new_qty,
                available_qty=0 if self.t_plus > 0 else new_qty,
                avg_cost=round_money(cost_per_share),
                last_price=fill.price,
            )
            self._today_buys[symbol] = self._today_buys.get(symbol, 0) + fill.qty
        else:
            position = self.positions.get(symbol)
            if position is None or position.qty < fill.qty:
                raise ExecutionError("持仓不足，无法卖出 %s" % symbol)
            self.release(fill.order_id)
            self.cash = round_money(self.cash + fill.gross_amount - fees)
            realized = round_money((fill.price - position.avg_cost) * fill.qty - fees)
            self.realized_pnl = round_money(self.realized_pnl + realized)
            remaining = position.qty - fill.qty
            if remaining <= 0:
                self.positions.pop(symbol, None)
            else:
                self.positions[symbol] = position.evolve(
                    qty=remaining,
                    available_qty=max(0, position.available_qty - fill.qty),
                    last_price=fill.price,
                )
        self.fees_paid = round_money(self.fees_paid + fees)
        self.turnover = round_money(self.turnover + fill.gross_amount)
        self._last_prices[symbol] = fill.price
        self.last_dt = fill.dt
        self._refresh_peak()
        return realized

    def sync_from_broker(self, cash: float, positions: Iterable[Position],
                         frozen_cash: float = 0.0,
                         equity: Optional[float] = None,
                         dt: Optional[datetime] = None) -> None:
        """用券商的权威数据覆盖本地账户（实盘对账）。

        传入 equity 时以券商总资产为准，避免用本地价格算出错误的权益；
        下一次本地成交或盯市前，权益显示保持与券商一致。
        """
        self.cash = round_money(max(0.0, float(cash)))
        self.frozen_cash = round_money(max(0.0, float(frozen_cash)))
        books: Dict[str, Position] = {}
        for position in positions:
            if position is None or not position.symbol:
                continue
            books[position.symbol] = position
            if position.last_price > 0:
                self._last_prices[position.symbol] = position.last_price
        self.positions = books
        if dt is not None:
            self.last_dt = dt
        if equity is None:
            self._equity_override = None
            self._refresh_peak()
        else:
            self._equity_override = round_money(float(equity))
            if self._equity_override > self.peak_equity:
                self.peak_equity = self._equity_override

    # ---------------------------------------------------------- 盯市

    def mark_to_market(self, prices: Dict[str, float],
                       dt: Optional[datetime] = None) -> None:
        for symbol, price in prices.items():
            if price is None or price <= 0:
                continue
            self._last_prices[symbol] = float(price)
            position = self.positions.get(symbol)
            if position is not None:
                self.positions[symbol] = position.evolve(last_price=float(price))
        if dt is not None:
            self.last_dt = dt
        self._refresh_peak()

    def settle_day(self, dt: Optional[datetime] = None) -> None:
        """新交易日开始：昨日买入变为可用，重置当日盈亏基准。"""
        for symbol, position in list(self.positions.items()):
            if position.available_qty != position.qty:
                self.positions[symbol] = position.evolve(available_qty=position.qty)
        self._today_buys.clear()
        self.day_start_equity = self.equity
        if dt is not None:
            self.last_dt = dt

    def _refresh_peak(self) -> None:
        equity = self.equity
        if equity > self.peak_equity:
            self.peak_equity = equity

    # ---------------------------------------------------------- 快照

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def day_pnl(self) -> float:
        return round_money(self.equity - self.day_start_equity)

    @property
    def day_pnl_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return self.day_pnl / self.day_start_equity

    @property
    def unrealized_pnl(self) -> float:
        return round_money(sum(p.unrealized_pnl for p in self.positions.values()))

    def snapshot(self, dt: Optional[datetime] = None) -> AccountSnapshot:
        moment = dt or self.last_dt or datetime.now()
        total = self.equity
        self._refresh_peak()
        drawdown = max(0.0, 1.0 - total / self.peak_equity) if self.peak_equity > 0 else 0.0
        return AccountSnapshot(
            dt=moment, cash=self.cash, frozen_cash=self.frozen_cash,
            market_value=self.position_value, equity=total,
            realized_pnl=self.realized_pnl, unrealized_pnl=self.unrealized_pnl,
            day_pnl=self.day_pnl, peak_equity=self.peak_equity,
            drawdown=drawdown, positions=tuple(self.positions.values()),
        )

    def reset(self, initial_cash: Optional[float] = None) -> None:
        self.__init__(initial_cash or self.initial_cash, self.t_plus)
