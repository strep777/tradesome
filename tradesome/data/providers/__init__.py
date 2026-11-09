"""数据源适配器集合。"""

from __future__ import annotations

from .csv_provider import CsvProvider, export_bars_csv, inspect_csv, read_csv_bars
from .synthetic_provider import SyntheticProvider

__all__ = [
    "SyntheticProvider",
    "CsvProvider",
    "read_csv_bars",
    "inspect_csv",
    "export_bars_csv",
]
