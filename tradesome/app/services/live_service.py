"""模拟盘服务：组装账户/券商/风控/路由，驱动 LiveEngine 并持久化运行记录。"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict, List, Optional

from ...core.errors import ExecutionError, StrategyError
from ...core.events import (LIVE_STARTED, LIVE_STOPPED, ORDER_FILLED, ORDER_REJECTED,
                            ORDER_SUBMITTED)
from ...core.ids import new_session_id
from ...core.logging import get_logger
from ...execution.live_engine import LiveEngine
from ...execution.paper_broker import PaperBroker
from ...execution.router import OrderRouter
from ...portfolio.account import Account
from ...risk.manager import RiskManager
from ...risk.rules import RiskLimits

LOGGER = get_logger("app.live")


class LiveService:
    def __init__(self, container) -> None:
        self.c = container
        self._lock = threading.RLock()
        self._engine: Optional[LiveEngine] = None
        self._session_id: Optional[str] = None
        self._unsubscribers: List = []
        self._snapshots = 0

    # ---------------------------------------------------------- 状态

    def is_running(self) -> bool:
        with self._lock:
            return self._engine is not None and self._engine.status in ("running", "paused")

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def status(self) -> dict:
        with self._lock:
            if self._engine is None:
                return {"status": "idle", "session_id": None}
            data = self._engine.status_dict()
            data["initial_cash"] = self._engine.account.initial_cash
            data["pnl"] = (self._engine.account.equity
                           - self._engine.account.initial_cash)
            return data

    def equity_curve(self) -> List[tuple]:
        if not self._session_id:
            return []
        return self.c.repos.equity.list_by_session(self._session_id)

    # ---------------------------------------------------------- 启停

    def start(self, strategy_key: str, params: Optional[Dict[str, object]] = None,
              symbols: Optional[List[str]] = None, days: int = 250,
              speed: float = 1.0,
              initial_cash: Optional[float] = None) -> str:
        if self.is_running():
            raise ExecutionError("模拟盘已经在运行，请先停止当前会话")
        key = strategy_key
        strategy = self.c.strategies.create(key, params or {})
        symbols = list(symbols or [])
        if not symbols:
            symbols = self._default_symbols()
        bars: Dict[str, list] = {}
        for symbol in symbols:
            rows = self.c.services.market.repo.get_bars(symbol, "1d", limit=max(30, days))
            if len(rows) >= 2:
                bars[symbol] = rows
        if not bars:
            raise ExecutionError("所选标的没有行情数据，请先在数据中心生成或导入")
        cash = float(initial_cash or self.c.config.trading.initial_cash)
        account = Account(cash, t_plus=1)
        fee = self.c.config.trading
        broker = PaperBroker(account, self.c.instruments, self._fee_profile(),
                             max_volume_ratio=fee.max_volume_ratio, bus=self.c.bus)
        limits = RiskLimits.from_config(self.c.config.risk)
        risk = RiskManager(limits=limits, bus=self.c.bus)
        router = OrderRouter(account=account, broker=broker, risk=risk,
                             instruments=self.c.instruments,
                             audit=self.c.repos.audit, bus=self.c.bus)
        session_id = new_session_id()
        self.c.repos.sessions.create(session_id, key, params or {}, list(bars.keys()),
                                     cash, speed=speed)
        engine = LiveEngine(session_id, strategy, router, broker, account, bars,
                            speed=speed, bus=self.c.bus,
                            tick_callback=self._on_tick)
        self._subscribe(session_id)
        engine.prepare()
        with self._lock:
            self._engine = engine
            self._session_id = session_id
            self._snapshots = 0
        engine.start()
        self.c.bus.emit(LIVE_STARTED, session_id=session_id, strategy=key,
                        symbols=list(bars.keys()))
        self.c.repos.audit.write("live", "start",
                                 detail="%s 策略 %s 标的 %d 个" % (
                                     session_id, key, len(bars)))
        LOGGER.info("模拟盘启动 %s", session_id)
        return session_id

    def pause(self) -> bool:
        engine = self._engine
        if engine is None:
            return False
        engine.pause()
        self.c.repos.sessions.update_status(engine.session_id, "PAUSED")
        return True

    def resume(self) -> bool:
        engine = self._engine
        if engine is None:
            return False
        engine.resume()
        self.c.repos.sessions.update_status(engine.session_id, "RUNNING")
        return True

    def stop(self) -> bool:
        with self._lock:
            engine = self._engine
        if engine is None:
            return False
        engine.stop()
        engine.join(3.0)
        status = "FINISHED" if engine.status == "finished" else "STOPPED"
        try:
            self.c.repos.sessions.finish(engine.session_id, engine.account.equity,
                                         status=status)
        except Exception:  # noqa: BLE001
            LOGGER.exception("更新模拟盘会话失败")
        self._persist_orders(engine)
        self._unsubscribe()
        self.c.bus.emit(LIVE_STOPPED, session_id=engine.session_id,
                        status=status, equity=engine.account.equity)
        self.c.repos.audit.write("live", "stop",
                                 detail="%s 结束权益 %.2f" % (engine.session_id,
                                                              engine.account.equity))
        with self._lock:
            self._engine = None
        LOGGER.info("模拟盘停止 %s", engine.session_id)
        return True

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("关闭模拟盘失败")

    # ---------------------------------------------------------- 会话数据

    def positions(self) -> List[dict]:
        """当前模拟盘持仓（只读快照），供界面展示。"""
        engine = self._engine
        if engine is None:
            return []
        rows = []
        for position in engine.account.position_list:
            rows.append({
                "symbol": position.symbol, "qty": position.qty,
                "available": position.available_qty,
                "avg_cost": position.avg_cost, "last": position.last_price,
                "pnl": position.unrealized_pnl,
                "pnl_pct": position.unrealized_pct,
                "value": position.market_value,
            })
        return rows

    def orders(self, limit: int = 100) -> list:
        session_id = self._session_id
        if not session_id:
            return []
        return self.c.repos.orders.list_by_session(session_id, limit=limit)

    def risk_state(self) -> dict:
        """运行中的风控状态；未运行时返回配置层面的摘要。"""
        engine = self._engine
        if engine is None:
            return {"running": False,
                    "kill_switch": bool(self.c.config.risk.kill_switch),
                    "rejections": 0, "rule_hits": {},
                    "drawdown": 0.0, "day_pnl_pct": 0.0,
                    "gross_exposure": 0.0}
        state = engine.router.risk.state()
        state["running"] = True
        state["drawdown"] = engine.account.drawdown
        state["day_pnl_pct"] = engine.account.day_pnl_pct
        state["gross_exposure"] = engine.account.gross_exposure
        return state

    def apply_risk_limits(self) -> None:
        """配置改动后同步到运行中的引擎。"""
        engine = self._engine
        if engine is None:
            return
        engine.router.risk.set_limits(RiskLimits.from_config(self.c.config.risk))

    def trip_kill_switch(self, reason: str = "手动触发") -> bool:
        engine = self._engine
        if engine is None:
            return False
        engine.router.risk.trip_kill_switch(reason)
        return True

    def reset_kill_switch(self) -> bool:
        engine = self._engine
        if engine is None:
            return False
        engine.router.risk.reset_kill_switch()
        return True




    # ---------------------------------------------------------- 事件订阅

    def _subscribe(self, session_id: str) -> None:
        def on_order(event) -> None:
            order = (event.payload or {}).get("order")
            if order is None:
                return
            try:
                self.c.repos.orders.save(order)
            except Exception:  # noqa: BLE001
                LOGGER.exception("保存订单失败")

        def on_fill(event) -> None:
            payload = event.payload or {}
            fill = payload.get("fill")
            order = payload.get("order")
            try:
                if order is not None:
                    self.c.repos.orders.save(order)
                if fill is not None:
                    self.c.repos.fills.save_many([fill])
            except Exception:  # noqa: BLE001
                LOGGER.exception("保存成交失败")

        self._unsubscribers = [
            self.c.bus.subscribe(ORDER_SUBMITTED, on_order),
            self.c.bus.subscribe(ORDER_FILLED, on_fill),
            self.c.bus.subscribe(ORDER_REJECTED, on_order),
        ]

    def _unsubscribe(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        self._unsubscribers = []

    # ---------------------------------------------------------- 内部

    def _on_tick(self, payload: dict) -> None:
        self._snapshots += 1
        if self._snapshots % 5 != 0:
            return
        session_id = payload.get("session_id") or self._session_id
        moment = payload.get("dt") or datetime.now()
        if isinstance(moment, str):
            try:
                moment = datetime.fromisoformat(moment)
            except ValueError:
                moment = datetime.now()
        equity = float(payload.get("equity") or 0.0)
        cash = float(payload.get("cash") or 0.0)
        market_value = max(0.0, equity - cash)
        engine = self._engine
        pnl = (equity - engine.account.initial_cash) if engine else 0.0
        drawdown = engine.account.drawdown if engine else 0.0
        try:
            self.c.repos.equity.add(session_id, moment, cash, market_value, equity,
                                    pnl, drawdown)
        except Exception:  # noqa: BLE001
            LOGGER.exception("保存权益快照失败")

    def _persist_orders(self, engine: LiveEngine) -> None:
        try:
            self.c.repos.orders.save_many(engine.broker.all_orders())
        except Exception:  # noqa: BLE001
            LOGGER.exception("保存模拟盘订单失败")

    def _default_symbols(self) -> List[str]:
        from ...data.catalog import default_symbols
        rows = [r for r in self.c.services.market.coverage()]
        if rows:
            return [r["symbol"] for r in rows[:3]]
        return default_symbols(3)

    def _fee_profile(self):
        from ...core.types import FeeProfile
        trading = self.c.config.trading
        return FeeProfile(commission_rate=trading.commission_rate,
                          min_commission=trading.min_commission,
                          stamp_tax_rate=trading.stamp_tax_rate,
                          transfer_fee_rate=trading.transfer_fee_rate,
                          slippage_bps=trading.slippage_bps)
