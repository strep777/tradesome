"""CSV 行情导入：尽量宽容地接受"券商/同花顺/通达信/a kshare"导出的表格。

宽容但不放纵：
- 表头支持中英文别名、大小写、空格；
- 编码依次尝试 utf-8-sig / utf-8 / gbk；
- 无法解析的行不静默丢弃，而是计数并给出原因；
- 单文件上限 100MB，防止误拖入巨型文件把内存打满。
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ...core.errors import DataError, ValidationError
from ...core.types import Bar
from ..ports import BaseProvider

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ROWS = 2_000_000
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")
_DELIMITERS = (",", "\t", ";")

_DATE_ALIASES = ("date", "datetime", "time", "dt", "trade_date", "tradedate",
                 "日期", "时间", "交易日期", "交易日")
_OPEN_ALIASES = ("open", "o", "开盘", "开盘价")
_HIGH_ALIASES = ("high", "h", "最高", "最高价")
_LOW_ALIASES = ("low", "l", "最低", "最低价")
_CLOSE_ALIASES = ("close", "c", "收盘", "收盘价", "最新价")
_VOLUME_ALIASES = ("volume", "vol", "v", "成交量", "成交量(手)", "成交量（手）")
_AMOUNT_ALIASES = ("amount", "turnover", "成交额", "成交额(元)", "成交额（元）", "金额")

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%Y%m%d", "%Y.%m.%d", "%d/%m/%Y",
)


def _normalize(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _match(header: Sequence[str], aliases: Sequence[str]) -> Optional[int]:
    normalized = [_normalize(h) for h in header]
    for alias in aliases:
        target = _normalize(alias)
        if target in normalized:
            return normalized.index(target)
    return None


def _detect_encoding(path: Path) -> str:
    for encoding in _ENCODINGS:
        try:
            with path.open("r", encoding=encoding) as handle:
                handle.read(4096)
            return encoding
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise DataError("文件读取失败：%s" % exc, hint="请确认文件未被占用") from exc
    return "utf-8"


def _sniff_delimiter(sample: str) -> str:
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    best, best_count = ",", 0
    for delimiter in _DELIMITERS:
        count = first_line.count(delimiter)
        if count > best_count:
            best, best_count = delimiter, count
    return best


def parse_number(text: Any) -> Optional[float]:
    if text is None:
        return None
    raw = str(text).strip().replace(",", "").replace("，", "").replace("元", "")
    if not raw or raw in ("-", "--", "N/A", "nan", "None"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_datetime(text: Any) -> Optional[datetime]:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit() and len(raw) >= 12:  # 20250102150000
        try:
            return datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
        except ValueError:
            pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def read_csv_bars(path: Union[str, Path], symbol: Optional[str] = None,
                  timeframe: str = "1d") -> List[Bar]:
    """读取 CSV 为 Bar 列表（时间升序、去重）。"""
    bars, _errors = read_csv_bars_ex(path, symbol, timeframe)
    return bars


def read_csv_bars_ex(path: Union[str, Path], symbol: Optional[str] = None,
                     timeframe: str = "1d") -> Tuple[List[Bar], List[str]]:
    """读取 CSV，同时返回逐行错误说明（最多 20 条）。"""
    target = Path(path)
    if not target.exists():
        raise DataError("文件不存在：%s" % target.name, hint="请重新选择 CSV 文件")
    size = target.stat().st_size
    if size <= 0:
        raise DataError("文件为空：%s" % target.name)
    if size > MAX_FILE_BYTES:
        raise DataError("文件过大（%.1f MB），上限 100 MB" % (size / 1024 / 1024),
                        hint="请先按标的拆分为多个 CSV，或只导入近 5 年数据")
    if target.suffix.lower() not in (".csv", ".txt", ".tsv"):
        raise DataError("只支持 .csv/.txt/.tsv 文件，当前为 %s" % target.suffix)

    encoding = _detect_encoding(target)
    try:
        text = target.read_text(encoding=encoding, errors="replace")
    except OSError as exc:
        raise DataError("文件读取失败：%s" % exc) from exc
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise DataError("文件没有表头：%s" % target.name, hint="请保留第一行表头")
    except csv.Error as exc:
        raise DataError("CSV 解析失败：%s" % exc) from exc

    idx_date = _match(header, _DATE_ALIASES)
    idx_open = _match(header, _OPEN_ALIASES)
    idx_high = _match(header, _HIGH_ALIASES)
    idx_low = _match(header, _LOW_ALIASES)
    idx_close = _match(header, _CLOSE_ALIASES)
    idx_volume = _match(header, _VOLUME_ALIASES)
    idx_amount = _match(header, _AMOUNT_ALIASES)
    missing = [
        label for label, index in (
            ("日期", idx_date), ("开盘", idx_open), ("最高", idx_high),
            ("最低", idx_low), ("收盘", idx_close),
        ) if index is None
    ]
    if missing:
        raise DataError("缺少必需的列：%s" % "、".join(missing),
                        hint="表头需包含：日期/开盘/最高/最低/收盘，可含成交量、成交额")

    fallback_symbol = str(symbol or target.stem).strip()
    if "." not in fallback_symbol:
        from ...core.instruments import guess_exchange
        fallback_symbol = "%s.%s" % (fallback_symbol, guess_exchange(fallback_symbol))

    bars: List[Bar] = []
    errors: List[str] = []
    seen: Dict[datetime, int] = {}
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not str(c).strip() for c in row):
            continue
        if len(row) < len(header):
            row = list(row) + [""] * (len(header) - len(row))

        def cell(index: Optional[int]) -> str:
            return row[index] if index is not None and index < len(row) else ""

        moment = parse_datetime(cell(idx_date))
        if moment is None:
            if len(errors) < 20:
                errors.append("第 %d 行：日期无法解析（%r）" % (line_no, cell(idx_date)))
            continue
        prices = {}
        bad = False
        for label, index in (("开盘", idx_open), ("最高", idx_high),
                             ("最低", idx_low), ("收盘", idx_close)):
            value = parse_number(cell(index))
            if value is None or value <= 0:
                if len(errors) < 20:
                    errors.append("第 %d 行：%s价无效（%r）" % (line_no, label, cell(index)))
                bad = True
                break
            prices[label] = value
        if bad:
            continue
        volume = parse_number(cell(idx_volume)) or 0.0
        amount = parse_number(cell(idx_amount)) or 0.0
        try:
            bar = Bar(
                symbol=fallback_symbol, dt=moment,
                open=prices["开盘"], high=prices["最高"], low=prices["最低"],
                close=prices["收盘"], volume=max(0.0, volume), amount=max(0.0, amount),
                timeframe=timeframe,
            )
        except ValidationError as exc:
            if len(errors) < 20:
                errors.append("第 %d 行：%s" % (line_no, exc.message))
            continue
        if moment in seen:
            bars[seen[moment]] = bar
        else:
            seen[moment] = len(bars)
            bars.append(bar)

    if not bars:
        detail = "；".join(errors[:5]) if errors else "没有有效数据行"
        raise DataError("CSV 中没有可用的 K 线：%s" % detail)
    bars.sort(key=lambda b: b.dt)
    return bars, errors


def inspect_csv(path: Union[str, Path]) -> Dict[str, Any]:
    """不导入，只看结构：给数据中心"预览"用。"""
    target = Path(path)
    if not target.exists():
        raise DataError("文件不存在：%s" % target.name)
    encoding = _detect_encoding(target)
    try:
        with target.open("r", encoding=encoding, errors="replace") as handle:
            sample = handle.read(64 * 1024)
    except OSError as exc:
        raise DataError("文件读取失败：%s" % exc) from exc
    delimiter = _sniff_delimiter(sample)
    rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter))
    header = rows[0] if rows else []
    columns = {
        "date": _match(header, _DATE_ALIASES),
        "open": _match(header, _OPEN_ALIASES),
        "high": _match(header, _HIGH_ALIASES),
        "low": _match(header, _LOW_ALIASES),
        "close": _match(header, _CLOSE_ALIASES),
        "volume": _match(header, _VOLUME_ALIASES),
        "amount": _match(header, _AMOUNT_ALIASES),
    }
    return {
        "path": str(target),
        "name": target.name,
        "size_bytes": target.stat().st_size,
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": header,
        "mapped": {k: (header[v] if v is not None else None) for k, v in columns.items()},
        "missing": [k for k in ("date", "open", "high", "low", "close") if columns[k] is None],
        "preview_rows": rows[1:6],
        "estimated_rows": max(0, len(rows) - 1),
    }


def export_bars_csv(bars: Iterable[Bar], path: Union[str, Path]) -> Path:
    """导出 K 线为 UTF-8 BOM 的 CSV（Excel 直接打开不乱码）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "代码"])
        for bar in bars:
            writer.writerow([
                bar.dt.strftime("%Y-%m-%d"), "%.2f" % bar.open, "%.2f" % bar.high,
                "%.2f" % bar.low, "%.2f" % bar.close, int(bar.volume),
                "%.2f" % bar.amount, bar.symbol,
            ])
    return target


class CsvProvider(BaseProvider):
    """把单个 CSV 当作一个标的的数据源（供数据页快速预览/初始化）。"""

    name = "csv"

    def __init__(self, path: Union[str, Path], symbol: Optional[str] = None,
                 registry=None) -> None:
        super().__init__(registry)
        self.path = Path(path)
        self.symbol = symbol
        self._bars: Optional[List[Bar]] = None

    def _load(self) -> List[Bar]:
        if self._bars is None:
            self._bars = read_csv_bars(self.path, self.symbol)
        return self._bars

    def list_instruments(self):
        bars = self._load()
        symbol = bars[0].symbol if bars else (self.symbol or "")
        instrument = self._instrument(symbol) if symbol else None
        return [instrument] if instrument else []

    def fetch_bars(self, symbol: str, start: date, end: date,
                   timeframe: str = "1d") -> List[Bar]:
        bars = self._load()
        first = start.date() if isinstance(start, datetime) else start
        last = end.date() if isinstance(end, datetime) else end
        picked = [b for b in bars if first <= b.dt.date() <= last]
        if timeframe and timeframe != "1d":
            from .resample import resample
            return resample(picked, timeframe)
        return picked
