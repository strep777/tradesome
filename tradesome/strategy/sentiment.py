"""情绪分：把"消息面"抽象成一个 -1..1 的数字。

离线环境无法联网抓新闻，所以默认返回中性 0；用户可以用 CSV 提供自己的
情绪打分（一列分数），策略只依赖这个统一接口，换数据源不需要改策略。
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable

from ..core.errors import DataError
from ..core.logging import get_logger

LOGGER = get_logger("strategy.sentiment")


@dataclass(frozen=True)
class SentimentScore:
    symbol: str
    dt: date
    score: float = 0.0
    source: str = "neutral"
    note: str = ""

    def __post_init__(self) -> None:
        if not -1.0 <= self.score <= 1.0:
            raise DataError("情绪分必须在 -1 到 1 之间：%s" % self.score)

    @property
    def label(self) -> str:
        if self.score >= 0.5:
            return "乐观"
        if self.score >= 0.15:
            return "偏多"
        if self.score <= -0.5:
            return "悲观"
        if self.score <= -0.15:
            return "偏空"
        return "中性"


@runtime_checkable
class SentimentProvider(Protocol):
    name: str

    def get(self, symbol: str, dt: date) -> SentimentScore:
        ...


class OfflineNeutralProvider:
    """离线默认：所有标的、所有日期都是中性。"""

    name = "offline-neutral"

    def get(self, symbol: str, dt: date) -> SentimentScore:
        return SentimentScore(symbol=symbol, dt=dt, score=0.0, source=self.name)


class CsvSentimentProvider:
    """CSV 情绪表：symbol,date,score[,note]。

    同一天没有记录时回退到该标的最近一条（不超过 7 天），否则中性。
    """

    name = "csv"

    def __init__(self, path: str) -> None:
        self.path = path
        self._rows: Dict[Tuple[str, date], SentimentScore] = {}
        self._by_symbol: Dict[str, List[Tuple[date, SentimentScore]]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            raise DataError("情绪文件不存在：%s" % self.path)
        with open(self.path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader, start=2):
                symbol = (row.get("symbol") or row.get("代码") or "").strip()
                raw_date = (row.get("date") or row.get("日期") or "").strip()
                raw_score = (row.get("score") or row.get("分数") or "").strip()
                if not symbol or not raw_date or not raw_score:
                    continue
                try:
                    day = _parse_date(raw_date)
                    score = max(-1.0, min(1.0, float(raw_score)))
                except ValueError:
                    LOGGER.warning("情绪文件第 %d 行格式错误，已跳过", index)
                    continue
                item = SentimentScore(symbol=symbol, dt=day, score=score,
                                      source=self.name, note=(row.get("note") or "").strip())
                self._rows[(symbol, day)] = item
                self._by_symbol.setdefault(symbol, []).append((day, item))
        for items in self._by_symbol.values():
            items.sort(key=lambda pair: pair[0])

    def get(self, symbol: str, dt: date) -> SentimentScore:
        exact = self._rows.get((symbol, dt))
        if exact is not None:
            return exact
        items = self._by_symbol.get(symbol)
        if items:
            for day, item in reversed(items):
                if day <= dt and (dt - day).days <= 7:
                    return item
        return SentimentScore(symbol=symbol, dt=dt, score=0.0, source="neutral")


def _parse_date(text: str) -> date:
    cleaned = text.replace("/", "-").strip()[:10]
    return datetime.strptime(cleaned, "%Y-%m-%d").date()


_DEFAULT: SentimentProvider = OfflineNeutralProvider()


def get_default_provider() -> SentimentProvider:
    return _DEFAULT


def set_default_provider(provider: SentimentProvider) -> None:
    global _DEFAULT
    _DEFAULT = provider


def score_for(symbol: str, dt: date) -> float:
    return get_default_provider().get(symbol, dt).score
