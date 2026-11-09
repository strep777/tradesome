"""双均线趋势策略：快线上穿慢线买入，下穿卖出，叠加 ATR 止损。"""

from __future__ import annotations

from typing import List

from ...core.types import Bar
from ..base import ParamSpec, Strategy, StrategyMeta, crossed_down, crossed_up, last


class MaCrossStrategy(Strategy):
    meta = StrategyMeta(
        key="ma_cross",
        name="双均线趋势",
        category="趋势",
        risk_level="中",
        description="快慢均线金叉买入、死叉卖出，用长期均线过滤方向，用 ATR 控制单笔亏损。",
        principle="趋势一旦形成会延续：价格站上长期均线且短期动能转强时跟进，跌破止损离场。",
        works_when="单边上涨或下跌趋势明显、波动率中等的市场。",
        fails_when="横盘震荡、频繁假突破时会被反复止损；跳空缺口会让止损价失效。",
        tags=("趋势", "金叉", "止损"),
    )

    def param_specs(self) -> List[ParamSpec]:
        return [
            ParamSpec("fast", "快线周期", "int", 5, 2, 60, help="短期均线天数"),
            ParamSpec("slow", "慢线周期", "int", 20, 5, 250, help="长期均线天数，必须大于快线"),
            ParamSpec("trend", "趋势过滤均线", "int", 120, 0, 250, help="0 表示不过滤；收盘价需在均线上方才买"),
            ParamSpec("atr_stop", "ATR 止损倍数", "float", 2.5, 0.5, 10.0, step=0.5),
            ParamSpec("weight", "单标的仓位", "float", 0.25, 0.05, 0.50, step=0.05,
                     help="按总资产比例买入"),
        ]

    def on_configure(self) -> None:
        if self.p["fast"] >= self.p["slow"]:
            self.p["fast"] = max(2, self.p["slow"] - 1)

    def on_bar(self, bar: Bar) -> None:
        symbol = bar.symbol
        p = self.p
        fast = self.indicator.sma(symbol, p["fast"])
        slow = self.indicator.sma(symbol, p["slow"])
        if last(fast) is None or last(slow) is None:
            return
        position = self.position_of(symbol)
        if position is not None and position.qty > 0:
            self._check_exit(symbol, bar, position)
            return
        if p["trend"] > 0:
            trend = self.indicator.sma(symbol, p["trend"])
            trend_value = last(trend)
            if trend_value is None or bar.close <= trend_value:
                return
        if crossed_up(fast, slow):
            self.buy(symbol, weight=p["weight"], reason="均线金叉")

    def _check_exit(self, symbol: str, bar: Bar, position) -> None:
        fast = self.indicator.sma(symbol, self.p["fast"])
        slow = self.indicator.sma(symbol, self.p["slow"])
        atr = last(self.indicator.atr(symbol, 14))
        stop_price = None
        if atr is not None and atr > 0:
            stop_price = position.avg_cost - self.p["atr_stop"] * atr
        if stop_price is not None and bar.close <= stop_price:
            self.close(symbol, reason="ATR 止损（%.2f）" % stop_price)
            return
        if crossed_down(fast, slow):
            self.close(symbol, reason="均线死叉")
