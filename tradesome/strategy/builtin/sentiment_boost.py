"""动量 + 情绪增强：把技术动量与"消息面打分"加权后轮动。

情绪分由 strategy.sentiment 提供；离线默认中性，因此默认行为≈纯动量，
用户导入 CSV 情绪表后即可获得"新闻/研报情绪"的增益。
"""

from __future__ import annotations

from typing import Dict, List

from ...core.types import Bar
from ..base import ParamSpec, Strategy, StrategyMeta, last
from ..sentiment import score_for


class SentimentBoostStrategy(Strategy):
    meta = StrategyMeta(
        key="sentiment_boost",
        name="动量+情绪增强",
        category="情绪",
        risk_level="中",
        description="在动量排名的基础上叠加情绪分（-1~1），选出'走势强且消息面不差'的标的。",
        principle="价格动量反映资金行为，情绪分反映信息扩散；两者共振时持续性更好。",
        works_when="有稳定的舆情/研报打分数据，且情绪对价格有 1–2 周领先性。",
        fails_when="情绪数据滞后或质量差时反而引入噪声；纯技术行情下与动量轮动无异。",
        tags=("动量", "情绪", "轮动"),
    )

    def param_specs(self) -> List[ParamSpec]:
        return [
            ParamSpec("lookback", "动量回看天数", "int", 20, 5, 120),
            ParamSpec("top_k", "持有数量", "int", 2, 1, 6),
            ParamSpec("rebalance_days", "调仓间隔", "int", 5, 1, 60, unit="天"),
            ParamSpec("sentiment_weight", "情绪权重", "float", 0.3, 0.0, 1.0, step=0.05),
            ParamSpec("weight", "单标的仓位", "float", 0.22, 0.10, 0.30, step=0.02),
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
        day = ts.date() if hasattr(ts, "date") else ts
        momentum: Dict[str, float] = {}
        for symbol in self.state.get("universe", symbols):
            value = last(self.indicator.roc(symbol, self.p["lookback"]))
            if value is not None:
                momentum[symbol] = value
        if not momentum:
            return
        values = list(momentum.values())
        low, high = min(values), max(values)
        span = high - low
        scores: Dict[str, float] = {}
        for symbol, value in momentum.items():
            norm = 0.5 if span <= 1e-9 else (value - low) / span
            try:
                mood = score_for(symbol, day)
            except Exception:  # noqa: BLE001 - 情绪源异常不应中断交易
                mood = 0.0
            scores[symbol] = (1 - self.p["sentiment_weight"]) * norm + self.p["sentiment_weight"] * mood
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        winners = [s for s, _ in ranked[: int(self.p["top_k"])]]
        for position in list(self.positions):
            if position.symbol not in winners:
                self.close(position.symbol, reason="掉出综合排名")
        for symbol in winners:
            position = self.position_of(symbol)
            if position is None or position.qty <= 0:
                self.buy(symbol, weight=self.p["weight"], reason="综合评分前 %d" % self.p["top_k"])
