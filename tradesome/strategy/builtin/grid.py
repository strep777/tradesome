"""网格交易：价格每跌一格买一份，每涨一格卖一份，天然适合震荡市。"""

from __future__ import annotations

from typing import Any, Dict, List

from ...core.types import Bar
from ..base import ParamSpec, Strategy, StrategyMeta


class GridStrategy(Strategy):
    meta = StrategyMeta(
        key="grid",
        name="等比网格",
        category="网格",
        risk_level="高",
        description="以首日价格为锚，每下跌一格买入固定仓位，每上涨一格卖出半仓，反复收割波动。",
        principle="震荡市价格在区间内往复；用固定百分比网格把波动切成可重复的利润单元。",
        works_when="有明确上下沿、长期横盘的标的（宽基 ETF、红利股）。",
        fails_when="单边下跌会一路加仓被套；单边上涨会过早卖光。必须配合最大回撤止损。",
        tags=("网格", "震荡", "ETF"),
    )

    def param_specs(self) -> List[ParamSpec]:
        return [
            ParamSpec("grid_pct", "网格间距", "float", 3.0, 0.5, 15.0, step=0.5, unit="%"),
            ParamSpec("levels", "最大加仓层数", "int", 5, 1, 20),
            ParamSpec("weight", "每格仓位", "float", 0.08, 0.02, 0.25, step=0.01),
            ParamSpec("sell_ratio", "每格卖出比例", "float", 0.5, 0.2, 1.0, step=0.1),
            ParamSpec("max_loss_pct", "网格总止损", "float", 15.0, 5.0, 40.0, step=1.0, unit="%"),
        ]

    def on_start(self) -> None:
        self.state.setdefault("grids", {})

    def on_configure(self) -> None:
        self.state.setdefault("grids", {})

    def on_bar(self, bar: Bar) -> None:
        symbol = bar.symbol
        p = self.p
        gap = p["grid_pct"] / 100.0
        grid = self.state["grids"].get(symbol)
        if grid is None:
            grid = {"anchor": bar.close, "buys": 0, "first": bar.close}
            self.state["grids"][symbol] = grid
        anchor = grid["anchor"]
        position = self.position_of(symbol)
        if position is not None and position.qty > 0:
            loss_pct = (bar.close / position.avg_cost - 1.0) * 100.0
            if loss_pct <= -p["max_loss_pct"]:
                self.close(symbol, reason="网格总止损 %.1f%%" % loss_pct)
                grid.update({"anchor": bar.close, "buys": 0, "first": bar.close})
                return
            if bar.close >= anchor * (1 + gap):
                if self.sell(symbol, weight=p["sell_ratio"], reason="网格止盈") is not None:
                    grid["anchor"] = bar.close
                    grid["buys"] = max(0, int(grid["buys"]) - 1)
                return
        else:
            if bar.close <= anchor * (1 - gap) and grid["buys"] < p["levels"]:
                if self.buy(symbol, weight=p["weight"], reason="网格加仓第 %d 格" % (grid["buys"] + 1)) is not None:
                    grid["anchor"] = bar.close
                    grid["buys"] = int(grid["buys"]) + 1
