"""撮合引擎：把"挂着的订单"按下一根 K 线撮合成交。

规则尽量贴近 A 股现实：
- 市价单在下一根 bar 开盘成交，并承担滑点；
- 限价单需要价格穿越才成交；
- 停牌（成交量为 0）不成交；
- 触及涨跌停时，买不进/卖不出；
- 单根 bar 最多吃掉成交量的 max_volume_ratio，超出部分留到下一根。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.ids import short_id
from ..core.logging import get_logger
from ..core.money import round_money
from ..core.types import (
    ORDER_LIMIT, ORDER_MARKET, SIDE_BUY, Bar, Fill, Instrument, Order, FeeProfile,
)
from .costs import apply_slippage, buy_cost, limit_fill_price, sell_cost

LOGGER = get_logger("backtest.matching")


@dataclass(frozen=True)
class MatchOutcome:
    fill: Optional[Fill] = None
    rejected: bool = False
    reason: str = ""

    @property
    def filled(self) -> bool:
        return self.fill is not None


class MatchingEngine:
    def __init__(self, fee: FeeProfile, max_volume_ratio: float = 0.05) -> None:
        self.fee = fee
        self.max_volume_ratio = max(0.0, min(1.0, float(max_volume_ratio)))

    def match(self, order: Order, bar: Bar, instrument: Instrument,
              available_cash: float, available_qty: int,
              prev_close: Optional[float] = None) -> MatchOutcome:
        if bar.symbol != order.symbol:
            return MatchOutcome(rejected=True, reason="行情与订单标的不一致")
        if bar.volume <= 0:
            return MatchOutcome(reason="停牌或无成交，订单继续挂起")

        limit_up, limit_down = self._limits(instrument, prev_close, bar)

        if order.order_type == ORDER_MARKET:
            price = apply_slippage(bar.open, order.side, self.fee, instrument.tick_size)
            if price >= limit_up - 1e-9 and order.side == SIDE_BUY:
                return MatchOutcome(reason="开盘涨停，买单无法成交")
            if price <= limit_down + 1e-9 and order.side != SIDE_BUY:
                return MatchOutcome(reason="开盘跌停，卖单无法成交")
        else:
            limit_price = float(order.limit_price or 0)
            if order.side == SIDE_BUY:
                if bar.low > limit_price:
                    return MatchOutcome(reason="限价未触及")
                if limit_price > limit_up + 1e-9:
                    return MatchOutcome(rejected=True, reason="限价高于涨停价")
            else:
                if bar.high < limit_price:
                    return MatchOutcome(reason="限价未触及")
                if limit_price < limit_down - 1e-9:
                    return MatchOutcome(rejected=True, reason="限价低于跌停价")
            price = limit_fill_price(bar.open, limit_price, order.side)

        price = self._align(price, instrument.tick_size)
        if price <= 0:
            return MatchOutcome(rejected=True, reason="成交价异常")

        quantity = int(order.remaining)
        lot = max(1, instrument.lot_size)
        cap = int(bar.volume * self.max_volume_ratio // lot) * lot
        if instrument.is_index:
            cap = quantity
        if cap <= 0 and not instrument.is_index:
            return MatchOutcome(reason="本根 K 线可成交量不足，订单继续挂起")
        quantity = min(quantity, cap) if not instrument.is_index else quantity
        quantity = (quantity // lot) * lot
        if quantity <= 0:
            return MatchOutcome(reason="可成交量不足一手，订单继续挂起")

        amount = round_money(price * quantity)
        if order.side == SIDE_BUY:
            commission, transfer, fees = buy_cost(amount, self.fee)
            stamp = 0.0
            if amount + fees > available_cash + 1e-6:
                affordable = int((available_cash * 0.999) / price // lot) * lot
                if affordable <= 0:
                    return MatchOutcome(rejected=True, reason="现金不足")
                quantity = affordable
                amount = round_money(price * quantity)
                commission, transfer, fees = buy_cost(amount, self.fee)
        else:
            if quantity > available_qty:
                quantity = (available_qty // lot) * lot
                if quantity <= 0:
                    return MatchOutcome(rejected=True, reason="可卖持仓不足（T+1）")
                amount = round_money(price * quantity)
            fees, stamp, commission = sell_cost(amount, self.fee)
            transfer = round_money(amount * self.fee.transfer_fee_rate)

        reference = bar.open if order.order_type == ORDER_MARKET else price
        slippage_cost = round_money(abs(price - reference) * quantity)
        fill = Fill(
            fill_id=short_id(), order_id=order.order_id, symbol=order.symbol,
            side=order.side, qty=quantity, price=price, dt=bar.dt,
            commission=commission, stamp_tax=stamp, transfer_fee=transfer,
            slippage_cost=slippage_cost,
        )
        return MatchOutcome(fill=fill)

    @staticmethod
    def _limits(instrument: Instrument, prev_close: Optional[float],
                bar: Bar) -> tuple:
        base = prev_close if prev_close and prev_close > 0 else bar.close
        pct = instrument.price_limit_pct
        tick = instrument.tick_size
        up = int((base * (1 + pct)) / tick + 1e-9) * tick
        down = int((base * (1 - pct)) / tick + 1e-9) * tick
        return round(up, 4), round(max(tick, down), 4)

    @staticmethod
    def _align(price: float, tick: float) -> float:
        if tick <= 0:
            return round(price, 4)
        return round(int(price / tick + 0.5) * tick, 4)
