"""A 股交易日历与交易时段。

节假日来源：上交所/深交所历年休市安排（2020–2025 已按实际公告录入；
2026–2027 为预排，若与实际不符只需修改 _EXTRA_HOLIDAYS，不影响其他模块）。
周末一律休市（调休上班日也不交易）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, List, Optional, Sequence, Set, Union

DateLike = Union[date, datetime]

# 各年休市日（仅列出落在工作日的日期；周末已天然休市）
_HOLIDAY_TEXT = {
    2020: "2020-01-01 2020-01-24 2020-01-27 2020-01-28 2020-01-29 2020-01-30 "
          "2020-01-31 2020-04-06 2020-05-01 2020-05-04 2020-05-05 2020-06-25 "
          "2020-06-26 2020-10-01 2020-10-02 2020-10-05 2020-10-06 2020-10-07 "
          "2020-10-08",
    2021: "2021-01-01 2021-02-11 2021-02-12 2021-02-15 2021-02-16 2021-02-17 "
          "2021-04-05 2021-05-03 2021-05-04 2021-05-05 2021-06-14 2021-09-20 "
          "2021-09-21 2021-10-01 2021-10-04 2021-10-05 2021-10-06 2021-10-07",
    2022: "2022-01-03 2022-01-31 2022-02-01 2022-02-02 2022-02-03 2022-02-04 "
          "2022-04-04 2022-04-05 2022-05-02 2022-05-03 2022-05-04 2022-06-03 "
          "2022-09-12 2022-10-03 2022-10-04 2022-10-05 2022-10-06 2022-10-07",
    2023: "2023-01-02 2023-01-23 2023-01-24 2023-01-25 2023-01-26 2023-01-27 "
          "2023-04-05 2023-05-01 2023-05-02 2023-05-03 2023-06-22 2023-06-23 "
          "2023-09-29 2023-10-02 2023-10-03 2023-10-04 2023-10-05 2023-10-06",
    2024: "2024-01-01 2024-02-09 2024-02-12 2024-02-13 2024-02-14 2024-02-15 "
          "2024-02-16 2024-04-04 2024-04-05 2024-05-01 2024-05-02 2024-05-03 "
          "2024-06-10 2024-09-16 2024-09-17 2024-10-01 2024-10-02 2024-10-03 "
          "2024-10-04 2024-10-07",
    2025: "2025-01-01 2025-01-28 2025-01-29 2025-01-30 2025-01-31 2025-02-03 "
          "2025-02-04 2025-04-04 2025-05-01 2025-05-02 2025-05-05 2025-06-02 "
          "2025-10-01 2025-10-02 2025-10-03 2025-10-06 2025-10-07 2025-10-08",
    # 预排：春节以除夕/初一为中心，国庆 10-01..10-08，其余按公历节日
    2026: "2026-01-01 2026-01-02 2026-02-16 2026-02-17 2026-02-18 2026-02-19 "
          "2026-02-20 2026-04-06 2026-05-01 2026-05-04 2026-05-05 2026-06-19 "
          "2026-09-25 2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07 "
          "2026-10-08",
    2027: "2027-01-01 2027-02-08 2027-02-09 2027-02-10 2027-02-11 2027-02-12 "
          "2027-04-05 2027-05-03 2027-05-04 2027-05-05 2027-06-09 2027-09-15 "
          "2027-10-01 2027-10-04 2027-10-05 2027-10-06 2027-10-07 2027-10-08",
}


def _parse_holidays() -> Set[date]:
    result: Set[date] = set()
    for text in _HOLIDAY_TEXT.values():
        for token in text.split():
            result.add(date.fromisoformat(token))
    return result


_EXTRA_HOLIDAYS: Set[date] = _parse_holidays()

# 交易时段边界（连续竞价 + 集合竞价）
_OPEN_CALL_START = time(9, 15)
_OPEN_CALL_END = time(9, 25)
_MORNING_START = time(9, 30)
_MORNING_END = time(11, 30)
_AFTERNOON_START = time(13, 0)
_CLOSE_CALL_START = time(14, 57)
_MARKET_CLOSE = time(15, 0)

PHASES = ("closed", "pre_open", "open_auction", "morning", "lunch",
          "afternoon", "close_auction", "after_hours")

PHASE_LABELS = {
    "closed": "休市",
    "pre_open": "盘前",
    "open_auction": "开盘集合竞价",
    "morning": "上午连续竞价",
    "lunch": "午间休市",
    "afternoon": "下午连续竞价",
    "close_auction": "收盘集合竞价",
    "after_hours": "已收盘",
}


def _as_date(value: DateLike) -> date:
    return value.date() if isinstance(value, datetime) else value


def add_holidays(days: Iterable[DateLike]) -> None:
    """补充休市日（例如交易所临时休市）。"""
    for item in days:
        _EXTRA_HOLIDAYS.add(_as_date(item))


def holidays(year: Optional[int] = None) -> List[date]:
    items = sorted(_EXTRA_HOLIDAYS)
    if year is None:
        return items
    return [d for d in items if d.year == year]


def is_weekend(day: DateLike) -> bool:
    return _as_date(day).weekday() >= 5


def is_holiday(day: DateLike) -> bool:
    return _as_date(day) in _EXTRA_HOLIDAYS


def is_trading_day(day: DateLike) -> bool:
    """是否交易日：非周末且不在休市表。"""
    d = _as_date(day)
    return d.weekday() < 5 and d not in _EXTRA_HOLIDAYS


def trading_days(start: DateLike, end: DateLike) -> List[date]:
    """闭区间内所有交易日（含首尾）。"""
    first, last = _as_date(start), _as_date(end)
    if first > last:
        first, last = last, first
    days: List[date] = []
    cursor = first
    while cursor <= last:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def count_trading_days(start: DateLike, end: DateLike) -> int:
    return len(trading_days(start, end))


def previous_trading_day(day: DateLike, include_self: bool = False) -> date:
    cursor = _as_date(day) if include_self else _as_date(day) - timedelta(days=1)
    for _ in range(400):
        if is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise ValueError("找不到上一个交易日：%s" % day)


def next_trading_day(day: DateLike, include_self: bool = False) -> date:
    cursor = _as_date(day) if include_self else _as_date(day) + timedelta(days=1)
    for _ in range(400):
        if is_trading_day(cursor):
            return cursor
        cursor += timedelta(days=1)
    raise ValueError("找不到下一个交易日：%s" % day)


def nearest_trading_day(day: DateLike) -> date:
    """若当天休市，取之后最近的交易日（用于回测区间修正）。"""
    d = _as_date(day)
    return d if is_trading_day(d) else next_trading_day(d, include_self=True)


def session_phase(moment: DateLike) -> str:
    """交易时段：closed/pre_open/open_auction/morning/lunch/afternoon/close_auction/after_hours。"""
    if isinstance(moment, date) and not isinstance(moment, datetime):
        return "closed"
    d = moment.date()
    if not is_trading_day(d):
        return "closed"
    t = moment.time()
    if t < _OPEN_CALL_START:
        return "pre_open"
    if t < _OPEN_CALL_END:
        return "open_auction"
    if t < _MORNING_START:
        return "pre_open"
    if t < _MORNING_END:
        return "morning"
    if t < _AFTERNOON_START:
        return "lunch"
    if t < _CLOSE_CALL_START:
        return "afternoon"
    if t < _MARKET_CLOSE:
        return "close_auction"
    return "after_hours"


def is_market_open(moment: DateLike) -> bool:
    """是否处于可撮合的连续竞价时段（不含集合竞价）。"""
    return session_phase(moment) in ("morning", "afternoon")


def phase_label(phase: str) -> str:
    return PHASE_LABELS.get(phase, phase)


def trading_days_between(start: datetime, end: datetime) -> Sequence[date]:
    return trading_days(start, end)
