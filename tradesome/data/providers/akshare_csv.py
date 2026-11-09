"""把 akshare 返回的记录（list[dict]）转换成标准 CSV。

这一层不 import akshare、也不 import pandas：应用本体保持零第三方依赖，
抓取在 tools/akshare_sync.py 里完成，这里只做可单测的清洗与落盘。
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ...core.types import Bar

DATE_KEYS = ("日期", "date", "交易日", "时间")
OPEN_KEYS = ("开盘", "open")
HIGH_KEYS = ("最高", "high")
LOW_KEYS = ("最低", "low")
CLOSE_KEYS = ("收盘", "close")
VOLUME_KEYS = ("成交量", "volume", "vol")
AMOUNT_KEYS = ("成交额", "amount", "turnover")

HEADER = ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]


def _pick(record: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, "", "-"):
            return record[key]
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in ("-", "--", "None", "nan", "NaN"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def records_to_bars(records: Iterable[Dict[str, Any]], symbol: str,
                    timeframe: str = "1d") -> Tuple[List[Bar], int]:
    """把原始记录转成按日期升序、去重的 Bar 列表；返回 (bars, 跳过条数)。"""
    by_date: Dict[datetime, Bar] = {}
    skipped = 0
    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            continue
        dt = _as_datetime(_pick(record, DATE_KEYS))
        values = {name: _as_float(_pick(record, keys)) for name, keys in (
            ("open", OPEN_KEYS), ("high", HIGH_KEYS), ("low", LOW_KEYS),
            ("close", CLOSE_KEYS), ("volume", VOLUME_KEYS), ("amount", AMOUNT_KEYS))}
        if dt is None or any(values[name] is None for name in ("open", "high", "low", "close")):
            skipped += 1
            continue
        try:
            bar = Bar(symbol=symbol, dt=dt, open=values["open"], high=values["high"],
                      low=values["low"], close=values["close"],
                      volume=values["volume"] or 0.0,
                      amount=values["amount"] or 0.0, timeframe=timeframe)
        except Exception:  # noqa: BLE001 - 单行坏数据不应中断整批
            skipped += 1
            continue
        by_date[dt] = bar
    bars = [by_date[key] for key in sorted(by_date)]
    return bars, skipped


def write_bars(bars: Sequence[Bar], path: Path) -> int:
    """写成 import_csv 能读的中文表头 CSV（utf-8-sig，Excel 打开不乱码）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        for bar in bars:
            writer.writerow([
                bar.dt.strftime("%Y-%m-%d"), "%.4f" % bar.open, "%.4f" % bar.high,
                "%.4f" % bar.low, "%.4f" % bar.close,
                "%.0f" % bar.volume, "%.2f" % bar.amount,
            ])
    return len(bars)


def write_records(records: Iterable[Dict[str, Any]], symbol: str,
                  path: Path) -> Tuple[int, int]:
    bars, skipped = records_to_bars(records, symbol)
    write_bars(bars, path)
    return len(bars), skipped
