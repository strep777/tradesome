"""滚动验证：把一段历史切成多个"训练窗 + 测试窗"，分别回测并汇总。

本工具不做参数寻优（避免引入过拟合），重点是看策略在不同时间段是否稳定：
同一套参数在多个测试窗里表现是否一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Sequence

from ..core.errors import BacktestError
from ..core.types import Bar, BacktestConfig, BacktestResult
from ..strategy.base import Strategy
from .engine import BacktestEngine


@dataclass(frozen=True)
class WalkWindow:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def label(self) -> str:
        return "第 %d 段 %s ~ %s" % (self.index + 1, self.test_start, self.test_end)


@dataclass
class WalkForwardResult:
    windows: List[WalkWindow]
    results: List[BacktestResult]

    def summary(self) -> Dict[str, Optional[float]]:
        if not self.results:
            return {"window_count": 0}
        returns = [float(r.metrics.get("total_return") or 0.0) for r in self.results]
        sharpes = [float(r.metrics.get("sharpe") or 0.0) for r in self.results]
        drawdowns = [float(r.metrics.get("max_drawdown") or 0.0) for r in self.results]
        positive = sum(1 for r in returns if r > 0)
        return {
            "window_count": len(self.results),
            "positive_windows": positive,
            "win_ratio": positive / len(self.results),
            "avg_return": sum(returns) / len(returns),
            "avg_sharpe": sum(sharpes) / len(sharpes),
            "worst_drawdown": max(drawdowns),
        }


def split_windows(start: date, end: date, train_days: int = 250,
                  test_days: int = 60, step_days: int = 60) -> List[WalkWindow]:
    """按自然日切窗；A 股一年约 244 个交易日，250 天足够覆盖一年。"""
    if start >= end:
        raise BacktestError("开始日期必须早于结束日期")
    if train_days < 30 or test_days < 10 or step_days < 5:
        raise BacktestError("窗口参数过小")
    windows: List[WalkWindow] = []
    train_start = start
    index = 0
    while True:
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append(WalkWindow(index, train_start, train_end, test_start, test_end))
        index += 1
        train_start = train_start + timedelta(days=step_days)
    return windows


def run_walkforward(config: BacktestConfig,
                    bars_by_symbol: Dict[str, Sequence[Bar]],
                    strategy_factory: Callable[[], Strategy],
                    instruments,
                    windows: Optional[List[WalkWindow]] = None,
                    train_days: int = 250, test_days: int = 60,
                    step_days: int = 60,
                    progress=None,
                    cancel=None,
                    risk_limits=None) -> WalkForwardResult:
    if windows is None:
        windows = split_windows(config.start, config.end, train_days, test_days, step_days)
    if not windows:
        raise BacktestError("历史数据太短，无法切出验证窗口；请缩短窗口或延长回测区间")

    results: List[BacktestResult] = []
    total = len(windows)
    for index, window in enumerate(windows):
        if cancel is not None and cancel():
            break
        window_config = BacktestConfig(
            symbols=config.symbols, start=window.test_start, end=window.test_end,
            initial_cash=config.initial_cash, timeframe=config.timeframe,
            strategy_key=config.strategy_key, strategy_params=dict(config.strategy_params),
            fee=config.fee, max_volume_ratio=config.max_volume_ratio,
            benchmark=config.benchmark, seed=config.seed,
            risk_enabled=config.risk_enabled,
        )
        engine = BacktestEngine(window_config, bars_by_symbol, strategy_factory(),
                                instruments, risk_limits=risk_limits)
        results.append(engine.run())
        if progress is not None:
            progress(round((index + 1) * 100.0 / total, 1), index + 1, total)
    return WalkForwardResult(windows=windows[:len(results)], results=results)
