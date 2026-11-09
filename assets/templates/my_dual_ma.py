"""示例：双均线 + 趋势过滤（可复制到 var/strategies 下使用）。

复制方法：把本文件复制到 `var/strategies/`（或界面「实验室 → 策略代码 → 保存」），
重启后在「策略库」里就能看到「示例·双均线趋势」。
"""

from tradesome.strategy.base import ParamSpec, Strategy, StrategyMeta


class DualMaExample(Strategy):
    meta = StrategyMeta(
        key="dual_ma_example",
        name="示例·双均线趋势",
        category="趋势",
        risk_level="中",
        description="快线上穿慢线且价格在长期均线之上时买入，快线下穿慢线时卖出。",
        principle="用快慢均线判断短期动量方向，再用长期均线过滤下跌趋势中的假突破。",
        works_when="有明确中长期趋势、回撤不超过 15% 的行情。",
        fails_when="长期横盘或剧烈震荡，均线反复交叉会产生连续小亏。",
        tags=("均线", "趋势", "示例"),
    )

    def param_specs(self):
        return [
            ParamSpec("fast", "快线周期", "int", 10, 3, 60, 1, unit="日",
                      help="越小越灵敏"),
            ParamSpec("slow", "慢线周期", "int", 30, 10, 120, 1, unit="日"),
            ParamSpec("trend", "趋势过滤周期", "int", 60, 20, 250, 5, unit="日",
                      help="0 表示不过滤"),
            ParamSpec("weight", "单标的仓位", "float", 0.20, 0.05, 0.30, 0.05),
        ]

    def on_bar(self, bar):
        p = self.p
        fast, slow, trend = p["fast"], p["slow"], p["trend"]
        need = max(slow, trend) + 2
        closes = self.history(bar.symbol, need)
        if len(closes) < need:
            return

        fast_now = sum(closes[-fast:]) / fast
        fast_prev = sum(closes[-fast - 1:-1]) / fast
        slow_now = sum(closes[-slow:]) / slow
        slow_prev = sum(closes[-slow - 1:-1]) / slow
        trend_ok = trend <= 0 or bar.close > sum(closes[-trend:]) / trend

        position = self.position_of(bar.symbol)
        if position is None and trend_ok and fast_prev <= slow_prev and fast_now > slow_now:
            self.buy(bar.symbol, weight=p["weight"], reason="快线上穿慢线")
        elif position is not None and fast_prev >= slow_prev and fast_now < slow_now:
            self.close(bar.symbol, reason="快线下穿慢线")
