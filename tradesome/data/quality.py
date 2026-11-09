"""数据质量检查：导入后立刻告诉用户"这份数据能不能用"。

只报告，不修改数据。严重问题（错误）阻止回测；提示问题（警告）允许继续，
但在回测页以横幅提醒，避免"垃圾进垃圾出"却毫不知情。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from ..core import calendar
from ..core.types import Bar

# 错误码
E_EMPTY = "E_EMPTY"
E_DATE = "E_DATE"
E_DUP = "E_DUP"
E_OHLC = "E_OHLC"
E_PRICE = "E_PRICE"
E_NEG = "E_NEG"
E_ZERO_VOL = "E_ZERO_VOL"
E_GAP = "E_GAP"
E_LIMIT = "E_LIMIT"
E_OUTLIER = "E_OUTLIER"
E_VOLUME = "E_VOLUME"
E_ORDER = "E_ORDER"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

SAFE_ERROR_CODES = frozenset({E_EMPTY, E_DATE, E_OHLC, E_PRICE, E_NEG, E_ORDER})


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = SEVERITY_WARNING
    dt: Optional[datetime] = None
    index: int = -1

    @property
    def is_error(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "message": self.message, "severity": self.severity,
            "dt": self.dt.isoformat(sep=" ") if self.dt else "",
            "index": self.index,
        }


@dataclass
class QualityReport:
    symbol: str = ""
    checked: int = 0
    issues: List[QualityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """无错误级问题即视为可用。"""
        return not any(i.is_error for i in self.issues)

    @property
    def errors(self) -> List[QualityIssue]:
        return [i for i in self.issues if i.is_error]

    @property
    def warnings(self) -> List[QualityIssue]:
        return [i for i in self.issues if not i.is_error]

    def add(self, code: str, message: str, severity: str = SEVERITY_WARNING,
            dt: Optional[datetime] = None, index: int = -1) -> None:
        self.issues.append(QualityIssue(code, message, severity, dt, index))

    def summary(self) -> str:
        if not self.issues:
            return "数据质量良好（%d 根 K 线）" % self.checked
        return "检查 %d 根 K 线：%d 个错误、%d 个提示" % (
            self.checked, len(self.errors), len(self.warnings))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "checked": self.checked, "ok": self.ok,
            "summary": self.summary(), "issues": [i.to_dict() for i in self.issues],
        }


def check_bars(bars: Sequence[Bar], symbol: str = "",
               price_limit_pct: float = 0.10,
               max_issues: int = 50) -> QualityReport:
    """检查一段 K 线。"""
    report = QualityReport(symbol=symbol or (bars[0].symbol if bars else ""),
                           checked=len(bars))
    if not bars:
        report.add(E_EMPTY, "没有可用的 K 线数据", SEVERITY_ERROR)
        return report

    if len(bars) == 1:
        report.add(E_EMPTY, "只有 1 根 K 线，无法计算指标与收益", SEVERITY_ERROR)
        return report

    limit = max(1, int(max_issues))

    def full() -> bool:
        return len(report.issues) >= limit

    prev: Optional[Bar] = None
    gap_count = 0
    zero_vol = 0
    for idx, bar in enumerate(bars):
        if full():
            break
        if prev is not None and bar.dt <= prev.dt:
            report.add(E_DATE, "时间未严格递增：%s 之后出现 %s" % (
                prev.dt.strftime("%Y-%m-%d"), bar.dt.strftime("%Y-%m-%d")),
                SEVERITY_ERROR, bar.dt, idx)
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            report.add(E_OHLC, "最高/最低价与开收盘价矛盾（%s）" % bar.dt.strftime("%Y-%m-%d"),
                       SEVERITY_ERROR, bar.dt, idx)
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            report.add(E_PRICE, "价格出现非正数（%s）" % bar.dt.strftime("%Y-%m-%d"),
                       SEVERITY_ERROR, bar.dt, idx)
        if bar.volume < 0 or bar.amount < 0:
            report.add(E_NEG, "成交量/成交额为负（%s）" % bar.dt.strftime("%Y-%m-%d"),
                       SEVERITY_ERROR, bar.dt, idx)
        if bar.volume == 0:
            zero_vol += 1
        if prev is not None and prev.close > 0:
            change = bar.close / prev.close - 1.0
            if abs(change) > price_limit_pct + 0.005:
                report.add(E_LIMIT, "%s 涨跌幅 %.1f%% 超过限制 %.0f%%，可能是未复权或错误数据"
                           % (bar.dt.strftime("%Y-%m-%d"), change * 100,
                              price_limit_pct * 100),
                           SEVERITY_WARNING, bar.dt, idx)
            elif abs(change) > 0.20:
                report.add(E_OUTLIER, "%s 单日波动 %.1f%% 异常" % (
                    bar.dt.strftime("%Y-%m-%d"), change * 100),
                    SEVERITY_WARNING, bar.dt, idx)
            if prev.volume > 0 and bar.volume > prev.volume * 30:
                report.add(E_VOLUME, "%s 成交量放大 30 倍，请确认是否拆股" %
                           bar.dt.strftime("%Y-%m-%d"), SEVERITY_WARNING, bar.dt, idx)
        if prev is not None:
            # 按交易日而非自然日判断缺口：春节休市 9~11 天是正常的。
            missing = calendar.count_trading_days(prev.dt.date(), bar.dt.date()) - 1
            if missing > 5:
                gap_count += 1
        prev = bar

    if zero_vol:
        report.add(E_ZERO_VOL, "有 %d 根 K 线成交量为 0（可能停牌）" % zero_vol,
                   SEVERITY_WARNING)
    if gap_count:
        report.add(E_GAP, "有 %d 处缺失超过 5 个交易日的数据缺口" % gap_count,
                   SEVERITY_WARNING)

    # 重复时间检查（排序后相邻比较之外的重复会被上面的 <= 覆盖）
    return report


def check_many(data: Dict[str, Sequence[Bar]], max_issues: int = 30) -> Dict[str, QualityReport]:
    return {symbol: check_bars(bars, symbol, max_issues=max_issues)
            for symbol, bars in data.items()}


def merge_reports(reports: Sequence[QualityReport]) -> QualityReport:
    merged = QualityReport(symbol=", ".join(r.symbol for r in reports if r.symbol))
    merged.checked = sum(r.checked for r in reports)
    for report in reports:
        merged.issues.extend(report.issues)
    return merged


def has_blocking_error(report: QualityReport) -> bool:
    return any(i.code in SAFE_ERROR_CODES for i in report.issues)
