"""模拟盘引擎：把历史行情按时间顺序"回放"给实盘链路。

与回测的区别：这里走的是真实的下单/风控/账户链路（OrderRouter + PaperBroker），
有会话、有暂停/继续、有实时事件，是"上线前最后一道演练"。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

from ..core.events import LIVE_STARTED, LIVE_STOPPED, LIVE_TICK
from ..core.logging import get_logger
from ..core.types import Bar
from ..backtest.engine import BacktestMarketView
from ..portfolio.account import Account
from ..strategy.base import Strategy
from ..strategy.context import StrategyContext
from .paper_broker import PaperBroker

LOGGER = get_logger("execution.live")

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_FINISHED = "finished"


class _AccountView:
    def __init__(self, account: Account) -> None:
        self._account = account

    @property
    def cash(self) -> float:
        return self._account.cash

    @property
    def equity(self) -> float:
        return self._account.equity

    @property
    def positions(self):
        return self._account.position_list

    def position_of(self, symbol: str):
        return self._account.position_of(symbol)

    def available_qty(self, symbol: str) -> int:
        return self._account.available_qty(symbol)


class LiveEngine:
    def __init__(self, session_id: str, strategy: Strategy, router,
                 broker: PaperBroker, account: Account,
                 bars_by_symbol: Dict[str, Sequence[Bar]],
                 speed: float = 1.0, bus=None,
                 tick_callback: Optional[Callable[[dict], None]] = None) -> None:
        self.session_id = session_id
        self.strategy = strategy
        self.router = router
        self.broker = broker
        self.account = account
        self.speed = max(0.1, min(20.0, float(speed)))
        self.bus = bus
        self.on_tick = tick_callback
        self.bars = {s: sorted(list(b), key=lambda x: x.dt) for s, b in bars_by_symbol.items()}
        self.timeline = sorted({b.dt for bars in self.bars.values() for b in bars})
        self.market = BacktestMarketView(self.bars)
        self.ctx = StrategyContext(
            strategy_id=getattr(strategy.meta, "key", "live"),
            session_id=session_id, gateway=router, account=_AccountView(account),
            market=self.market, instrument_lookup=None)
        self.status = STATUS_IDLE
        self.index = 0
        self.started_at: Optional[datetime] = None
        self.stopped_at: Optional[datetime] = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.last_tick: Dict[str, object] = {}

    # ------------------------------------------------------------ 生命周期

    def prepare(self) -> None:
        self.strategy.bind(self.ctx)
        self.strategy.on_start()

    def start(self) -> None:
        with self._lock:
            if self.status in (STATUS_RUNNING, STATUS_PAUSED):
                return
            self._stop.clear()
            self._pause.clear()
            self._thread = threading.Thread(target=self.run_forever,
                                            name="tradesome-live", daemon=True)
            self.status = STATUS_RUNNING
            self.started_at = self.started_at or datetime.now()
            self._thread.start()
            self._emit(LIVE_STARTED, status=self.status, session_id=self.session_id)

    def pause(self) -> None:
        if self.status == STATUS_RUNNING:
            self.status = STATUS_PAUSED
            self._pause.set()

    def resume(self) -> None:
        if self.status == STATUS_PAUSED:
            self.status = STATUS_RUNNING
            self._pause.clear()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------ 主循环

    def run_forever(self) -> None:
        try:
            self.prepare()
            while not self._stop.is_set() and self.index < len(self.timeline):
                if self._pause.is_set():
                    time.sleep(0.05)
                    continue
                self.step()
                time.sleep(max(0.02, 0.25 / self.speed))
        except Exception as exc:  # noqa: BLE001 - 实盘线程异常要落日志而不是静默死掉
            LOGGER.exception("模拟盘运行异常")
            self._emit(LIVE_STOPPED, status="error", message=str(exc))
        finally:
            self.status = STATUS_FINISHED if self.index >= len(self.timeline) else STATUS_STOPPED
            self.stopped_at = datetime.now()
            try:
                self.strategy.on_stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("策略 on_stop 异常")
            self._emit(LIVE_STOPPED, status=self.status, session_id=self.session_id)

    def step(self) -> None:
        if self.index >= len(self.timeline):
            return
        moment = self.timeline[self.index]
        prices: Dict[str, float] = {}
        today_bars: Dict[str, Bar] = {}
        for symbol, bars in self.bars.items():
            bar = self._bar_at(symbol, moment)
            if bar is None:
                continue
            today_bars[symbol] = bar
            self.market.advance(bar)
            # 成交事件由 PaperBroker 统一发布，避免重复
            self.broker.on_bar(bar)
            prices[symbol] = bar.close
        if today_bars:
            self.account.mark_to_market(prices, moment)
            self.ctx.begin_bar(moment)
            try:
                self.strategy.on_bars(today_bars, moment)
            except Exception:  # noqa: BLE001 - 单根 bar 异常不终止整个会话
                LOGGER.exception("策略执行异常")
        self.index += 1
        snapshot = self.account.snapshot(moment)
        self.last_tick = {
            "session_id": self.session_id, "dt": moment, "index": self.index,
            "total": len(self.timeline), "equity": snapshot.equity,
            "cash": snapshot.cash, "pnl": snapshot.unrealized_pnl,
            "progress": self.index / max(1, len(self.timeline)),
        }
        self._emit(LIVE_TICK, **self.last_tick)
        if self.on_tick is not None:
            try:
                self.on_tick(self.last_tick)
            except Exception:  # noqa: BLE001
                LOGGER.exception("tick 回调异常")

    def _bar_at(self, symbol: str, moment: datetime) -> Optional[Bar]:
        for bar in self.bars.get(symbol, ()):
            if bar.dt == moment:
                return bar
        return None

    def _emit(self, topic: str, **payload) -> None:
        if self.bus is not None:
            try:
                self.bus.emit(topic, **payload)
            except Exception:  # noqa: BLE001
                LOGGER.exception("发布事件失败")

    def status_dict(self) -> dict:
        return {
            "session_id": self.session_id, "status": self.status,
            "index": self.index, "total": len(self.timeline),
            "speed": self.speed, "equity": self.account.equity,
            "cash": self.account.cash, "positions": len(self.account.position_list),
            "started_at": self.started_at, "stopped_at": self.stopped_at,
        }
