"""回测服务：把回测放到后台线程跑，界面只订阅事件，不阻塞主线程。"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Dict, List, Optional

from ...backtest.engine import BacktestEngine
from ...backtest.walkforward import run_walkforward, split_windows
from ...core.errors import BacktestError, DataError
from ...core.events import (BACKTEST_FAILED, BACKTEST_FINISHED, BACKTEST_PROGRESS,
                            BACKTEST_STARTED)
from ...core.ids import new_run_id
from ...core.logging import get_logger
from ...core.types import BacktestConfig, BacktestResult

LOGGER = get_logger("app.backtest")


class BacktestService:
    def __init__(self, container) -> None:
        self.c = container
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._run_id: Optional[str] = None
        self._progress = 0.0
        self._error: Optional[str] = None
        self._result: Optional[BacktestResult] = None
        self._started_at: Optional[datetime] = None

    # ---------------------------------------------------------- 状态

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def run_id(self) -> Optional[str]:
        return self._run_id

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def latest_result(self) -> Optional[BacktestResult]:
        return self._result

    # ---------------------------------------------------------- 启动/取消

    def start(self, config: BacktestConfig,
              strategy_key: Optional[str] = None,
              params: Optional[Dict[str, object]] = None) -> str:
        if self.is_running():
            raise BacktestError("已有回测在运行，请等它结束或先取消")
        key = strategy_key or config.strategy_key
        if not key:
            raise BacktestError("没有指定策略")
        if not config.symbols:
            raise DataError("请至少选择一个标的")
        bars = self._load_bars(config)
        if not any(bars.values()):
            raise DataError("所选区间没有行情数据，请先在数据中心生成或导入")
        strategy = self.c.strategies.create(key, params or config.strategy_params or {})
        if not config.strategy_key:
            config = config.evolve(strategy_key=key, strategy_params=params or {})
        run_id = new_run_id()
        self._cancel.clear()
        self._error = None
        self._progress = 0.0
        self._run_id = run_id
        self._started_at = datetime.now()
        self.c.bus.emit(BACKTEST_STARTED, run_id=run_id, strategy=key,
                        symbols=list(config.symbols))
        thread = threading.Thread(target=self._worker, name="backtest-%s" % run_id,
                                  args=(run_id, config, strategy, bars), daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()
        return run_id

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def shutdown(self) -> None:
        self.cancel()
        self.join(2.0)

    # ---------------------------------------------------------- 历史

    def history(self, limit: int = 50) -> List[dict]:
        return self.c.repos.backtests.list_runs(limit=limit)

    def load(self, run_id: str) -> Optional[BacktestResult]:
        return self.c.repos.backtests.load(run_id)

    # ---------------------------------------------------------- 后台执行

    def _load_bars(self, config: BacktestConfig) -> Dict[str, list]:
        outcome: Dict[str, list] = {}
        for symbol in config.symbols:
            outcome[symbol] = self.c.services.market.repo.get_bars(
                symbol, config.timeframe)
        benchmark = config.benchmark
        if benchmark and benchmark not in outcome:
            rows = self.c.services.market.repo.get_bars(benchmark, config.timeframe)
            if rows:
                outcome[benchmark] = rows
        return outcome

    def _on_progress(self, pct: float, index: int, total: int) -> None:
        self._progress = pct
        self.c.bus.emit(BACKTEST_PROGRESS, run_id=self._run_id, pct=pct,
                        bar_index=index, total=total)

    def _worker(self, run_id: str, config: BacktestConfig, strategy, bars) -> None:
        try:
            engine = BacktestEngine(config, bars, strategy, self.c.instruments,
                                    bus=self.c.bus)
            result = engine.run(progress=self._on_progress,
                                cancel=self._cancel.is_set, run_id=run_id)
            self._result = result
            try:
                self.c.repos.backtests.save(result)
            except Exception:  # 保存失败不应让结果消失
                LOGGER.exception("回测结果落库失败")
                self.c.repos.audit.write("backtest", "save_failed", severity="WARN")
            self.c.repos.audit.write("backtest", "run",
                                     detail="%s %s 收益 %.2f%%" % (
                                         run_id, config.strategy_key,
                                         result.metrics.get("total_return", 0) * 100))
            self.c.bus.emit(BACKTEST_FINISHED, run_id=run_id,
                            metrics=dict(result.metrics),
                            cancelled=result.cancelled)
            LOGGER.info("回测完成 %s，用时 %.2fs", run_id, result.duration_sec)
        except Exception as exc:  # noqa: BLE001 - 后台线程必须兜底
            self._error = str(exc)
            LOGGER.error("回测失败：%s\n%s", exc, traceback.format_exc())
            self.c.repos.audit.write("backtest", "failed", detail=str(exc)[:500],
                                     severity="ERROR")
            self.c.bus.emit(BACKTEST_FAILED, run_id=run_id, message=str(exc))
        finally:
            self._progress = 100.0

    # ---------------------------------------------------------- 滚动验证

    def walkforward(self, config: BacktestConfig, train_days: int = 250,
                    test_days: int = 60, step_days: int = 60):
        bars = self._load_bars(config)
        if not any(bars.values()):
            raise DataError("所选区间没有行情数据")
        key = config.strategy_key
        if not key:
            raise BacktestError("没有指定策略")

        def factory(params=None):
            return self.c.strategies.create(key, params or config.strategy_params or {})

        windows = split_windows(config.start, config.end, train_days, test_days,
                                step_days)
        return run_walkforward(config, bars, factory, self.c.instruments,
                               windows=windows, cancel=self._cancel.is_set)
