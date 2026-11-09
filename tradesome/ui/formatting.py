"""数字与时间的显示格式：全界面统一，避免每个页面各写一套。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from . import theme


def money(value: Optional[float], digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "—"
    fmt = "{:+,." + str(digits) + "f}" if sign else "{:,.%df}" % digits
    return fmt.format(float(value))


def number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return ("{:,.%df}" % digits).format(float(value))


def pct(value: Optional[float], digits: int = 2, signed: bool = False) -> str:
    """value 是小数（0.0123 → 1.23%）。"""
    if value is None:
        return "—"
    fmt = "{:+.%df}%%" % digits if signed else "{:.%df}%%" % digits
    return fmt.format(float(value) * 100.0)


def compact(value: Optional[float]) -> str:
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 1e8:
        return "%.2f亿" % (value / 1e8)
    if abs(value) >= 1e4:
        return "%.2f万" % (value / 1e4)
    return "%.0f" % value


def shares(value: Optional[float]) -> str:
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 1e8:
        return "%.2f亿股" % (value / 1e8)
    if abs(value) >= 1e4:
        return "%.2f万股" % (value / 1e4)
    return "%d股" % int(value)


def date_text(value, with_time: bool = False) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = float(seconds)
    if seconds < 1:
        return "%d 毫秒" % int(seconds * 1000)
    if seconds < 60:
        return "%.1f 秒" % seconds
    return "%d 分 %d 秒" % (int(seconds // 60), int(seconds % 60))


def tone(value: Optional[float]) -> str:
    if value is None:
        return "muted"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "muted"


def tone_color(value: Optional[float]) -> str:
    return theme.color(tone(value))


def truncate(text: str, limit: int = 40) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


METRIC_LABELS = {
    "total_return": "累计收益", "annual_return": "年化收益",
    "annual_volatility": "年化波动", "sharpe": "夏普比率",
    "sortino": "索提诺比率", "max_drawdown": "最大回撤",
    "max_dd_duration": "最长回撤", "calmar": "卡玛比率",
    "win_rate": "胜率", "profit_factor": "盈亏比",
    "payoff_ratio": "平均盈亏比", "trade_count": "交易笔数",
    "turnover": "成交额", "total_fees": "总费用", "fee_ratio": "费用占比",
    "exposure": "平均仓位", "benchmark_return": "基准收益",
    "alpha": "年化超额", "beta": "Beta",
}
