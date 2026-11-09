"""动量轮动：每 N 天按区间涨幅排名，持有最强的 K 只，其余清仓。

这类策略天然分散，适合多标的组合；换手率高于单标的策略，需注意成本。
"""

from __future__ import annotations

from typing import Dict, List

from ...core.types import Bar
from ..base import ParamSpec, Strategy, StrategyMeta, last


class MomentumRotationStrategy(Strategy):
    meta = StrategyMeta(
        key="momentum_rotation",
        name="动量轮动",
        category="轮动",
        risk_level="中",
        description="每隔若干天比较全部标的的区间涨幅，买入最强的 K 只，卖出掉出排名的持仓。",
        principle="中期动量具有延续性：过去 1–3 个月表现最好的资产，未来一段时间仍可能领先。",
        works_when="行业/风格分化明显、趋势持续的行情。",
        fails_when="风格快速切换（一个月一变）时会在高点买入、低点卖出，换手成本高。",
        tags=("动量", "轮动", "多标的"),
    )

    def param_specs(self) -> List[ParamSpec]:
        return [
            ParamSpec("lookback", "动量回看天数", "int", 20, 5, 120),
            ParamSpec("top_k", "持有数量", "int", 2, 1, 8),
            ParamSpec("rebalance_days", "调仓间隔", "int", 5, 1, 60, unit="天"),
            ParamSpec("weight", "单标的仓位", "float", 0.24, 0.10, 0.30, step=0.02),
            ParamSpec("min_momentum", "最低动量要求", "float", 0.0, -20.0, 20.0, step=0.5, unit="%"),
        ]

    def on_start(self) -> None:
        self.state.setdefault("day", 0)
        self.state.setdefault("universe", [])

    def on_bars(self, bars: Dict[str, Bar], ts) -> None:
        symbols = sorted(bars)
        if symbols:
            self.state["universe"] = symbols
        self.state["day"] = int(self.state.get("day", 0)) + 1
        if (self.state["day"] - 1) % max(1, int(self.p["rebalance_days"])) != 0:
            return
        scores: Dict[str, float] = {}
        for symbol in self.state.get("universe", symbols):
            value = last(self.indicator.roc(symbol, self.p["lookback"]))
            if value is not None:
                scores[symbol] = value
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        winners = [s for s, score in ranked[: int(self.p["top_k"])]
                   if score >= self.p["min_momentum"]]
        for position in list(self.positions):
            if position.symbol not in winners:
                self.close(position.symbol, reason="掉出动量排名")
        for symbol in winners:
            position = self.position_of(symbol)
            if position is None or position.qty <= 0:
                self.buy(symbol, weight=self.p["weight"],
                         reason="动量排名前 %d（%.1f%%）" % (self.p["top_k"], scores.get(symbol, 0.0)))
