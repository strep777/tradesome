"""时钟抽象：回测用模拟时钟，实盘/界面用真实时钟。

把"现在几点"从业务代码里抽出来，是让同一套策略代码既能回测
又能模拟盘运行的关键（否则策略会偷偷调用 datetime.now()）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional


class Clock:
    """时钟协议（结构化子类型，无需显式继承）。"""

    def now(self) -> datetime:  # pragma: no cover - 协议声明
        raise NotImplementedError


class RealClock:
    """真实时钟。"""

    def now(self) -> datetime:
        return datetime.now()

    def sleep(self, seconds: float) -> None:
        import time

        if seconds > 0:
            time.sleep(seconds)

    def __repr__(self) -> str:
        return "RealClock()"


class SimulatedClock:
    """模拟时钟：只能前进，不能倒流。"""

    def __init__(self, start: datetime, speed: float = 1.0) -> None:
        if not isinstance(start, datetime):
            raise TypeError("SimulatedClock 需要 datetime 起点")
        self._now = start
        self._speed = float(speed) if speed > 0 else 1.0

    def now(self) -> datetime:
        return self._now

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        self._speed = float(speed) if speed > 0 else 1.0

    def advance_to(self, moment: datetime) -> datetime:
        """推进到指定时刻；早于当前时刻则原地不动（不抛错，避免边界抖动）。"""
        if not isinstance(moment, datetime):
            raise TypeError("advance_to 需要 datetime")
        if moment > self._now:
            self._now = moment
        return self._now

    def advance(self, **kwargs) -> datetime:
        """按 timedelta 参数推进，如 advance(days=1)。"""
        delta = timedelta(**kwargs)
        if delta.total_seconds() > 0:
            self._now = self._now + delta
        return self._now

    def reset(self, start: datetime) -> None:
        self._now = start

    def __repr__(self) -> str:
        return "SimulatedClock(%s, speed=%s)" % (self._now.isoformat(sep=" "), self._speed)


class ManualClock(RealClock):
    """可被测试手动设定的真实时钟替身。"""

    def __init__(self, now: Optional[datetime] = None) -> None:
        self._now = now or datetime(2025, 1, 2, 9, 30, 0)
        self.calls: List[datetime] = []

    def now(self) -> datetime:
        self.calls.append(self._now)
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment
