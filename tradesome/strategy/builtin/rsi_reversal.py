"""RSI 超卖反转：RSI 进入超卖区且出现止跌信号时买入，超买或止损时卖出。"""

from __future__ import annotations

from typing import List

from ...core.types import Bar
from ..base import ParamSpec, Strategy, StrategyMeta, last


class RsiReversalStrategy(Strategy):
    meta = StrategyMeta(
        key="rsi_reversal",
        name="RSI 超卖反转",
        category="均值回归",
        risk_level="中",
        description="RSI 跌破超卖线且当日收涨（止跌确认）时买入，RSI 回到超买区或触发止损时卖出。",
        principle="短期抛压过度后会出现修复性反弹；要求'当日收涨'是为了避免在下跌中途接刀。",
        works_when="震荡市、急跌后的技术性反弹。",
        fails_when="单边下跌趋势中 RSI 会长期钝化，抄底容易连续止损。",
        tags=("RSI", "超卖", "反弹"),
    )

    def param_specs(self) -> List[ParamSpec]:
        return [
            ParamSpec("period", "RSI 周期", "int", 14, 2, 60),
            ParamSpec("oversold", "超卖线", "float", 30.0, 5.0, 45.0, step=1.0),
            ParamSpec("overbought", "超买线", "float", 65.0, 55.0, 95.0, step=1.0),
            ParamSpec("stop_pct", "止损百分比", "float", 5.0, 1.0, 20.0, step=0.5, unit="%"),
            ParamSpec("weight", "单标的仓位", "float", 0.20, 0.05, 0.50, step=0.05),
            ParamSpec("confirm_up", "要求当日收涨", "bool", True),
        ]

    def on_bar(self, bar: Bar) -> None:
        symbol = bar.symbol
        p = self.p
        series = self.indicator.rsi(symbol, p["period"])
        value = last(series)
        if value is None:
            return
        position = self.position_of(symbol)
        if position is not None and position.qty > 0:
            loss_pct = (bar.close / position.avg_cost - 1.0) * 100.0
            if loss_pct <= -p["stop_pct"]:
                self.close(symbol, reason="止损 %.1f%%" % loss_pct)
            elif value >= p["overbought"]:
                self.close(symbol, reason="RSI 超买 %.1f" % value)
            return
        if value <= p["oversold"]:
            if p["confirm_up"]:
                history = self.history(symbol, 2)
                if len(history) < 2 or history[-1] <= history[-2]:
                    return
            self.buy(symbol, weight=p["weight"], reason="RSI 超卖 %.1f" % value)
