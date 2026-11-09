"""布林带均值回归：跌破下轨买入，回到中轨卖出，带固定百分比止损。"""

from __future__ import annotations

from typing import List

from ...core.types import Bar
from ..base import ParamSpec, Strategy, StrategyMeta, last


class BollReversionStrategy(Strategy):
    meta = StrategyMeta(
        key="boll_reversion",
        name="布林带回归",
        category="均值回归",
        risk_level="中",
        description="价格偏离统计中枢过远时反向买入，回到中轨即止盈，跌破止损线离场。",
        principle="短期价格围绕均值波动，极端偏离后大概率回归；用布林带把'极端'量化。",
        works_when="震荡市、宽幅整理、没有单边趋势的标的。",
        fails_when="单边下跌时'越跌越买'会连续止损；趋势行情中会过早卖出。",
        tags=("均值回归", "布林带", "逆势"),
    )

    def param_specs(self) -> List[ParamSpec]:
        return [
            ParamSpec("period", "布林周期", "int", 20, 5, 120),
            ParamSpec("k", "带宽倍数", "float", 2.0, 1.0, 3.5, step=0.1),
            ParamSpec("stop_pct", "止损百分比", "float", 6.0, 1.0, 20.0, step=0.5, unit="%"),
            ParamSpec("weight", "单标的仓位", "float", 0.20, 0.05, 0.50, step=0.05),
            ParamSpec("exit_mid", "回到中轨止盈", "bool", True),
        ]

    def on_bar(self, bar: Bar) -> None:
        symbol = bar.symbol
        p = self.p
        mid, upper, lower = self.indicator.boll(symbol, p["period"], p["k"])
        mid_value, upper_value, lower_value = last(mid), last(upper), last(lower)
        if mid_value is None or upper_value is None or lower_value is None:
            return
        position = self.position_of(symbol)
        if position is not None and position.qty > 0:
            loss_pct = (bar.close / position.avg_cost - 1.0) * 100.0
            if loss_pct <= -p["stop_pct"]:
                self.close(symbol, reason="跌破止损线 %.1f%%" % loss_pct)
            elif p["exit_mid"] and bar.close >= mid_value:
                self.close(symbol, reason="回到中轨止盈")
            return
        if bar.close <= lower_value:
            self.buy(symbol, weight=p["weight"], reason="跌破布林下轨")
