"""绩效指标：只依赖净值曲线与成交记录，便于单独测试。"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.types import TradeRecord

TRADING_DAYS = 252


def _daily_returns(equity: Sequence[float]) -> List[float]:
    returns: List[float] = []
    for i in range(1, len(equity)):
        base = equity[i - 1]
        returns.append((equity[i] / base - 1.0) if base else 0.0)
    return returns


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def drawdown_curve(equity_values: Sequence[float]) -> List[float]:
    peak = 0.0
    curve: List[float] = []
    for value in equity_values:
        peak = max(peak, value)
        curve.append(1.0 - value / peak if peak > 0 else 0.0)
    return curve


def max_drawdown(equity_values: Sequence[float]) -> Tuple[float, int]:
    """返回 (最大回撤, 最长回撤持续天数)。"""
    peak = 0.0
    worst = 0.0
    current = 0
    longest = 0
    for value in equity_values:
        if value >= peak:
            peak = value
            current = 0
        else:
            current += 1
            longest = max(longest, current)
            if peak > 0:
                worst = max(worst, 1.0 - value / peak)
    return worst, longest


def monthly_returns(curve: Sequence[Tuple[datetime, float]]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    month_start: Dict[str, float] = {}
    last: Dict[str, float] = {}
    for dt, equity in curve:
        key = "%04d-%02d" % (dt.year, dt.month)
        if key not in month_start:
            month_start[key] = equity
        last[key] = equity
    for key, start in month_start.items():
        end = last[key]
        result[key] = (end / start - 1.0) if start else 0.0
    return dict(sorted(result.items()))


def compute_metrics(curve: Sequence[Tuple[datetime, float]],
                    trades: Sequence[TradeRecord] = (),
                    initial_cash: float = 100000.0,
                    risk_free: float = 0.02,
                    benchmark_curve: Optional[Sequence[Tuple[datetime, float]]] = None,
                    exposure_series: Optional[Sequence[float]] = None) -> Dict[str, Optional[float]]:
    values = [float(v) for _, v in curve]
    if not values:
        return _empty_metrics()
    final = values[-1]
    total_return = final / initial_cash - 1.0 if initial_cash else 0.0
    days = max(1, len(values) - 1)
    years = days / TRADING_DAYS
    annual_return = (final / initial_cash) ** (1.0 / years) - 1.0 if years > 0 and initial_cash > 0 and final > 0 else total_return
    returns = _daily_returns(values)
    annual_vol = _std(returns) * math.sqrt(TRADING_DAYS)
    sharpe: Optional[float] = None
    if annual_vol > 1e-9:
        sharpe = (annual_return - risk_free) / annual_vol
    downside = [r for r in returns if r < 0]
    downside_vol = _std(downside) * math.sqrt(TRADING_DAYS) if downside else 0.0
    sortino = (annual_return - risk_free) / downside_vol if downside_vol > 1e-9 else None
    dd, dd_days = max_drawdown(values)
    calmar = annual_return / dd if dd > 1e-9 else None

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    win_rate = (len(wins) / len(trades)) if trades else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-9 else None
    avg_win = _mean([t.pnl for t in wins]) if wins else 0.0
    avg_loss = abs(_mean([t.pnl for t in losses])) if losses else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 1e-9 else None
    total_fees = sum(t.fees for t in trades)
    turnover = sum(abs(t.entry_price * t.qty) for t in trades)
    exposure = _mean(list(exposure_series)) if exposure_series else 0.0

    result: Dict[str, Optional[float]] = {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": dd,
        "max_dd_duration": float(dd_days),
        "calmar": calmar,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff,
        "trade_count": float(len(trades)),
        "turnover": turnover,
        "total_fees": total_fees,
        "fee_ratio": (total_fees / initial_cash) if initial_cash else 0.0,
        "exposure": exposure,
    }
    if benchmark_curve:
        bench_values = [float(v) for _, v in benchmark_curve]
        bench_return = bench_values[-1] / bench_values[0] - 1.0 if bench_values and bench_values[0] else 0.0
        result["benchmark_return"] = bench_return
        bench_returns = _daily_returns(bench_values)
        if len(bench_returns) == len(returns) and len(returns) > 2:
            beta = _cov(returns, bench_returns) / _var(bench_returns) if _var(bench_returns) > 1e-12 else None
            result["beta"] = beta
            if beta is not None:
                alpha_daily = (_mean(returns) - risk_free / TRADING_DAYS) - beta * (_mean(bench_returns) - risk_free / TRADING_DAYS)
                result["alpha"] = alpha_daily * TRADING_DAYS
    return result


def _cov(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    ma, mb = _mean(a[:n]), _mean(b[:n])
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)


def _var(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


def _empty_metrics() -> Dict[str, Optional[float]]:
    keys = ("total_return", "annual_return", "annual_volatility", "sharpe",
            "sortino", "max_drawdown", "max_dd_duration", "calmar", "win_rate",
            "profit_factor", "payoff_ratio", "trade_count", "turnover",
            "total_fees", "fee_ratio", "exposure")
    return {key: 0.0 for key in keys}


def format_metric(key: str, value: Optional[float]) -> str:
    """统一的指标展示格式（界面与报告共用）。"""
    if value is None:
        return "—"
    if key in ("total_return", "annual_return", "annual_volatility", "max_drawdown",
               "win_rate", "exposure", "benchmark_return", "alpha", "fee_ratio"):
        return "%.2f%%" % (value * 100)
    if key in ("sharpe", "sortino", "calmar", "profit_factor", "payoff_ratio", "beta"):
        return "%.2f" % value
    if key in ("trade_count", "max_dd_duration"):
        return "%d" % int(value)
    if key in ("turnover", "total_fees"):
        return "%.2f" % value
    return "%.4f" % value
