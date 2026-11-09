"""交易成本：佣金、印花税、过户费与滑点。

全系统唯一的费用实现；回测、模拟盘、报表都调用这里，避免"三处算法"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..core.money import ceil_price, floor_price, round_money
from ..core.types import SIDE_BUY, SIDE_SELL, FeeProfile


def buy_cost(amount: float, fee: FeeProfile) -> Tuple[float, float, float]:
    """买入费用：返回 (佣金, 过户费, 合计)。印花税只在卖出收。"""
    amount = abs(float(amount))
    commission = round_money(max(amount * fee.commission_rate, fee.min_commission))
    transfer = round_money(amount * fee.transfer_fee_rate)
    return commission, transfer, round_money(commission + transfer)


def sell_cost(amount: float, fee: FeeProfile) -> Tuple[float, float, float]:
    """卖出费用：返回 (佣金+过户费+印花税 合计, 印花税, 佣金)。"""
    amount = abs(float(amount))
    commission = round_money(max(amount * fee.commission_rate, fee.min_commission))
    stamp = round_money(amount * fee.stamp_tax_rate)
    transfer = round_money(amount * fee.transfer_fee_rate)
    return round_money(commission + stamp + transfer), stamp, commission


def total_fees(side: str, amount: float, fee: FeeProfile) -> float:
    if side == SIDE_BUY:
        return buy_cost(amount, fee)[2]
    return sell_cost(amount, fee)[0]


def apply_slippage(price: float, side: str, fee: FeeProfile,
                   tick: float = 0.01) -> float:
    """市价单按不利方向滑动，并按最小价位对齐。"""
    rate = fee.slippage_bps / 10000.0
    if side == SIDE_BUY:
        return ceil_price(price * (1.0 + rate), tick)
    return floor_price(price * (1.0 - rate), tick)


def limit_fill_price(open_price: float, limit_price: float, side: str) -> float:
    """限价单成交价：开盘更优则按开盘，否则按限价。"""
    if side == SIDE_BUY:
        return min(open_price, limit_price)
    return max(open_price, limit_price)


@dataclass(frozen=True)
class CostBreakdown:
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    slippage_cost: float = 0.0

    @property
    def total(self) -> float:
        return round_money(self.commission + self.stamp_tax + self.transfer_fee)
