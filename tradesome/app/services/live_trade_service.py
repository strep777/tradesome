"""实盘交易服务。

把网关、OMS、路由、风控、账户镜像和一个后台同步线程编排在一起；
UI 只跟本服务打交道，不直接碰 socket 或券商。

安全边界（与 docs/14 一致）：

* 实盘默认关闭（``config.live.enabled``），且每次启动都要由界面重新解锁（``arm``）；
* 只做 A 股/ETF 限价；市价单在 ``LiveBroker`` 里转成保护价限价，且要求实时价；
* 所有委托、撤单、熔断、连接变化都写审计；
* 自动交易按交易日 14:55 触发一次；本版 QMT 网关不订阅行情，缺少实时价时拒绝执行。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

from ...core import calendar as trading_calendar
from ...core.config import AppConfig
from ...core.errors import GatewayError, TradingError
from ...core.events import (
    ACCOUNT_UPDATED, AUTO_TRADE_TRIGGERED, GATEWAY_CONNECTED, GATEWAY_DISCONNECTED,
    GATEWAY_ERROR, LIVE_TRADE_STARTED, LIVE_TRADE_STOPPED, ORDER_FILLED,
    ORDER_UPDATED, ORDER_REJECTED, POSITION_UPDATED,
)
from ...core.ids import new_order_id, new_session_id
from ...core.types import (
    ORDER_LIMIT, ORDER_MARKET, SIDE_BUY, SIDE_SELL, FeeProfile, Order, OrderRequest,
    Position, ST_REJECTED,
)
from ...execution.router import OrderRouter
from ...gateway import create_gateway
from ...gateway.base import BaseGateway
from ...gateway.protocol import (
    AccountUpdate, GW_CONNECTED, OrderUpdate, PositionUpdate, TickUpdate, TradeUpdate,
)
from ...portfolio.account import Account
from ...risk import RiskLimits, RiskManager
from ...strategy.context import StrategyContext
from ...trading.live_broker import LiveBroker
from ...trading.live_market import LiveMarket
from ...trading.oms import OrderManager

LOGGER = logging.getLogger("tradesome.live_trade")


class LiveTradeService:
    """实盘会话：连接、下单、对账、自动交易。"""

    def __init__(self, container) -> None:
        self.container = container
        self.config: AppConfig = container.config
        self._lock = threading.RLock()
        self._gateway: Optional[BaseGateway] = None
        self._gateway_kind: str = ""
        self._oms = OrderManager()
        self._router: Optional[OrderRouter] = None
        self._account = Account(float(self.config.trading.initial_cash), t_plus=0)
        self._broker: Optional[LiveBroker] = None
        self._market: Optional[LiveMarket] = None
        self._strategy = None
        self._session_id = ""
        self._strategy_key = ""
        self._strategy_params: Dict[str, Any] = {}
        self._symbols: List[str] = []
        self._account_update: Optional[AccountUpdate] = None
        self._positions: Dict[str, PositionUpdate] = {}
        self._prices: Dict[str, float] = {}
        self._status = "idle"
        self._armed = False
        self._auto_trade = False
        self._auto_done = ""
        self._last_sync: Optional[datetime] = None
        self._last_error = ""
        self._sync_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._initial_cash = float(self.config.trading.initial_cash)
        self._order_counter: Optional[list] = None
        self._client_seq = 0

    # ---------------------------------------------------------- 只读状态

    @property
    def config_live(self):
        return self.config.live

    @property
    def connected(self) -> bool:
        gateway = self._gateway
        return bool(gateway is not None and gateway.is_connected)

    @property
    def running(self) -> bool:
        return bool(self._session_id) and self._status in (
            "connecting", "connected", "running", "disconnected")

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def status(self) -> str:
        return self._status

    def status_dict(self) -> Dict[str, Any]:
        gateway = self._gateway
        return {
            "status": self._status,
            "connected": self.connected,
            "armed": self._armed,
            "running": self.running,
            "session_id": self._session_id,
            "gateway": gateway.name if gateway is not None else "",
            "gateway_kind": self._gateway_kind or self.config.live.gateway_kind,
            "strategy_key": self._strategy_key,
            "symbols": list(self._symbols),
            "auto_trade": self._auto_trade,
            "auto_done": self._auto_done,
            "last_sync": self._last_sync,
            "error": self._last_error,
            "quote_mode": "realtime" if self._prices else "last_close",
            "initial_cash": self._initial_cash,
            "equity": self._account.equity,
            "cash": self._account.cash,
        }

    def account_snapshot(self) -> Dict[str, Any]:
        snapshot = self._account.snapshot(datetime.now())
        return {
            "equity": snapshot.equity,
            "cash": snapshot.cash,
            "frozen": snapshot.frozen_cash,
            "market_value": snapshot.market_value,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "realized_pnl": snapshot.realized_pnl,
            "drawdown": self._account.drawdown,
            "day_pnl_pct": self._account.day_pnl_pct,
        }

    def positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            updates = list(self._positions.values())
        result = []
        for position in updates:
            if position.qty <= 0:
                continue
            price = (self._prices.get(position.symbol)
                     or position.last_price or position.avg_cost)
            value = price * position.qty
            cost = position.avg_cost * position.qty
            result.append({
                "symbol": position.symbol,
                "qty": position.qty,
                "available": position.available_qty,
                "avg_cost": round(position.avg_cost, 3),
                "last_price": round(price, 3),
                "value": round(value, 2),
                "pnl": round(value - cost, 2),
                "pnl_pct": round((value - cost) / cost, 6) if cost else 0.0,
            })
        return result

    def orders(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = [self._order_payload(order) for order in self._oms.all()]
        rows.sort(key=lambda item: item["time"] or datetime.now(), reverse=True)
        return rows[:int(limit)]

    def trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = []
        for fill in self._oms.fills():
            rows.append({
                "trade_id": fill.trade_id,
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "qty": fill.qty,
                "price": fill.price,
                "fees": fill.commission,
                "time": fill.dt,
            })
        rows.sort(key=lambda item: item["time"] or datetime.now(), reverse=True)
        return rows[:int(limit)]

    def risk_state(self) -> Dict[str, Any]:
        if self._router is None:
            return {"running": False, "kill_switch": self.config.kill_switch,
                    "rejections": 0, "drawdown": 0.0, "rule_hits": {}}
        state = dict(self._router.risk.state())
        state["running"] = self.running
        state["drawdown"] = self._account.drawdown
        state["day_pnl_pct"] = self._account.day_pnl_pct
        state["gross_exposure"] = self._account.gross_exposure
        return state

    # ---------------------------------------------------------- 解锁

    def arm(self) -> None:
        """界面完成风险确认后调用；任何断线/停止都会自动解除。"""
        if not self.config.live.enabled:
            raise TradingError("实盘功能未启用，请先在「设置 → 实盘」中开启")
        self._armed = True

    def disarm(self) -> None:
        self._armed = False

    # ---------------------------------------------------------- 会话

    def start_session(self, gateway_kind: Optional[str] = None,
                      strategy_key: str = "",
                      qmt_path: Optional[str] = None,
                      qmt_account: Optional[str] = None,
                      qmt_account_type: Optional[str] = None,
                      params: Optional[Dict[str, Any]] = None,
                      symbols: Optional[List[str]] = None,
                      initial_cash: Optional[float] = None,
                      auto_trade: bool = False) -> Dict[str, Any]:
        if not self.config.live.enabled:
            raise TradingError("实盘功能未启用，请先在「设置 → 实盘」中开启")
        if not self._armed:
            raise TradingError("实盘尚未解锁，请先勾选风险提示并输入确认词")
        with self._lock:
            if self._gateway is not None:
                raise TradingError("实盘会话已在运行，请先停止")
            live = self.config.live
            kind = (gateway_kind or live.gateway_kind or "sim").strip().lower()
            self._initial_cash = float(initial_cash or self.config.trading.initial_cash)
            fee = FeeProfile(
                commission_rate=self.config.trading.commission_rate,
                min_commission=self.config.trading.min_commission,
                stamp_tax_rate=self.config.trading.stamp_tax_rate,
                transfer_fee_rate=self.config.trading.transfer_fee_rate,
            )
            if kind == "qmt":
                gateway = create_gateway(
                    kind,
                    path=qmt_path if qmt_path is not None else live.qmt_path,
                    account_id=(qmt_account if qmt_account is not None
                                else live.qmt_account),
                    account_type=(qmt_account_type if qmt_account_type is not None
                                  else live.qmt_account_type),
                )
            else:
                gateway = create_gateway(
                    kind, initial_cash=self._initial_cash, fee=fee)
            self._gateway = gateway
            self._gateway_kind = kind
            self._status = "connecting"
            self._last_error = ""
            self._auto_trade = bool(auto_trade)
            self._auto_done = ""
            self._strategy_key = strategy_key
            self._strategy_params = dict(params or {})
            self._symbols = list(symbols or [])
            self._strategy = None
            self._account = Account(self._initial_cash, t_plus=0)
            self._positions.clear()
            self._prices.clear()
            self._oms.clear()
            self._session_id = new_session_id()
            self._broker = LiveBroker(gateway, gateway_name=kind,
                                      instrument_lookup=self.container.instruments.get)
            risk = RiskManager(limits=RiskLimits.from_config(self.config.risk),
                               bus=self.container.bus)
            self._router = OrderRouter(
                self._account, self._broker, risk, self.container.instruments,
                audit=self.container.repos.audit, bus=self.container.bus,
                price_lookup=self._price_of,
            )
            gateway.set_listener(self._on_gateway)
            self.container.repos.sessions.create(
                self._session_id, strategy_key or "manual", dict(params or {}),
                list(self._symbols), self._initial_cash, mode="live", gateway=kind,
            )
            gateway.start()
            if kind == "sim":
                self._seed_sim_prices()
            if self._symbols:
                try:
                    gateway.subscribe(self._symbols)
                except Exception:  # noqa: BLE001 - 不支持行情的柜台
                    LOGGER.debug("网关不支持订阅：%s", kind)
            self._emit(LIVE_TRADE_STARTED, {
                "session_id": self._session_id, "gateway": kind,
                "symbols": list(self._symbols),
            })
            self._audit("live", "connect", "%s 策略=%s"
                        % (kind, strategy_key or "manual"))
            self._stop_event.clear()
            self._sync_thread = threading.Thread(
                target=self._sync_loop, name="live-sync", daemon=True)
            self._sync_thread.start()
            return self.status_dict()

    def stop_session(self, reason: str = "用户停止", status: str = "STOPPED") -> Dict[str, Any]:
        with self._lock:
            gateway = self._gateway
            session_id = self._session_id
            if gateway is None and not session_id:
                return self.status_dict()
            self._status = "stopping"
            self._stop_event.set()
        thread = self._sync_thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._sync_thread = None
        if gateway is not None:
            try:
                gateway.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("停止网关失败")
        changed = self._oms.mark_unknown(reason)
        self._persist_orders(changed)
        if session_id:
            try:
                self.container.repos.sessions.finish(
                    session_id, float(self._account.equity),
                    status=status, stopped_at=datetime.now())
            except Exception:  # noqa: BLE001
                LOGGER.exception("结束会话失败")
        self._emit(LIVE_TRADE_STOPPED, {"session_id": session_id, "reason": reason})
        self._audit("live", "stop", "%s：%s" % (session_id or "-", reason))
        with self._lock:
            self._gateway = None
            self._gateway_kind = ""
            self._router = None
            self._broker = None
            self._session_id = ""
            self._status = "idle"
            self._armed = False
            self._auto_trade = False
            self._strategy = None
        return self.status_dict()

    def emergency_stop(self, reason: str = "人工熔断") -> Dict[str, Any]:
        """撤掉所有在场委托并断开，用于异常行情或断线。"""
        gateway = self._gateway
        cancelled = 0
        if gateway is not None:
            try:
                cancelled = gateway.cancel_all()
            except GatewayError as exc:
                LOGGER.warning("紧急撤单失败：%s", exc)
        self._audit("live", "emergency_stop", "%s，撤销 %d 笔" % (reason, cancelled),
                    severity="WARNING")
        result = self.stop_session(reason, status="STOPPED")
        result["cancelled"] = cancelled
        return result

    # ---------------------------------------------------------- 下单/撤单

    def place_order(self, symbol: str, side: str, qty: int,
                    price: Optional[float] = None, order_type: str = ORDER_LIMIT,
                    reason: str = "", strategy_id: str = "") -> Order:
        if not self._armed:
            raise TradingError("实盘尚未解锁，不能下单")
        router = self._router
        if router is None:
            raise TradingError("实盘会话未启动，请先连接柜台")
        side = str(side or "").upper()
        if side not in (SIDE_BUY, SIDE_SELL):
            raise TradingError("买卖方向非法：%s" % side)
        order_type = ORDER_MARKET if str(order_type).upper() == ORDER_MARKET else ORDER_LIMIT
        if order_type == ORDER_MARKET and not self.config.live.allow_market_orders:
            raise TradingError("已禁用市价单；如需市价保护请到设置中开启")
        if order_type == ORDER_LIMIT and (price is None or price <= 0):
            raise TradingError("限价单必须填写价格")
        qty = int(qty)
        if qty <= 0:
            raise TradingError("委托数量必须大于 0")
        if price is not None and qty * float(price) > self.config.live.max_order_value:
            raise TradingError("单笔金额超过实盘上限 %.0f 元，请拆分或到设置中调整"
                               % self.config.live.max_order_value)
        today = datetime.now().date()
        if self._today_order_count(today) >= self.config.live.max_orders_per_day:
            raise TradingError("今日实盘委托已达上限 %d 笔"
                               % self.config.live.max_orders_per_day)
        moment = datetime.now()
        request = OrderRequest(
            symbol=symbol, side=side, qty=qty, order_type=order_type,
            limit_price=float(price) if (price and order_type == ORDER_LIMIT) else 0.0,
            reason=reason or "实盘手动下单",
            client_id=self._next_client_id(symbol, side),
        )
        order = router.submit(request, strategy_id=strategy_id or "manual",
                             session_id=self._session_id, price=price,
                             is_open_session=self._in_session(), now=moment)
        if order.status == ST_REJECTED:
            raise TradingError(order.reason or "订单被拒绝")
        order = self._oms.register(order)
        self._broker_remember(order)
        self._persist_orders([order])
        self._bump_order_counter(today)
        return order

    def cancel(self, order_id: str) -> bool:
        router = self._router
        if router is None:
            raise TradingError("实盘会话未启动")
        result = router.cancel(order_id)
        if result:
            order = self._oms.get(order_id)
            if order is not None:
                updated = order.evolve(status="CANCELLED", updated_at=datetime.now())
                self._oms.register(updated)
                self._persist_orders([updated])
                self._emit(ORDER_UPDATED, self._order_payload(updated))
            self._audit("live", "cancel", order_id)
        return result

    def cancel_all(self) -> int:
        gateway = self._gateway
        if gateway is None:
            return 0
        count = gateway.cancel_all()
        self._audit("live", "cancel_all", "撤销 %d 笔" % count)
        return count

    def feed_tick(self, symbol: str, price: float, volume: float = 0.0) -> bool:
        """模拟柜台专用：注入一笔行情，用于本地演练。"""
        gateway = self._gateway
        if gateway is None or not hasattr(gateway, "feed_tick"):
            return False
        if price <= 0:
            return False
        gateway.feed_tick(TickUpdate(symbol=symbol, last_price=float(price),
                                     bid=float(price), ask=float(price),
                                     volume=float(volume), dt=datetime.now()))
        return True

    def _seed_sim_prices(self) -> None:
        """模拟柜台启动时用最近收盘价做初始行情，避免"永远不成交"。"""
        symbols = list(self._symbols)
        if not symbols:
            symbols = self._default_symbols()
        for symbol in symbols:
            bars = self._load_bars(symbol, limit=1)
            if bars:
                self.feed_tick(symbol, bars[-1].close)

    # ---------------------------------------------------------- 自动交易

    def run_strategy_once(self, force: bool = False) -> Dict[str, Any]:
        """按当前策略跑一次日线决策（14:55 自动或手动触发）。"""
        if not self._armed or self._router is None:
            return {"ok": False, "message": "实盘未解锁或未连接"}
        key = self._strategy_key
        if not key:
            return {"ok": False, "message": "未选择策略"}
        if not self._symbols:
            self._symbols = self._default_symbols()
        symbols = list(self._symbols)
        bars_by_symbol = {}
        market = LiveMarket({})
        missing_price: List[str] = []
        is_sim = self._gateway is not None and self._gateway.name == "sim"
        for symbol in symbols:
            bars = self._load_bars(symbol)
            if not bars:
                continue
            bars_by_symbol[symbol] = bars
            market.set_bars(symbol, bars)
            price = self._prices.get(symbol)
            if price is None:
                if is_sim:
                    price = bars[-1].close
                    missing_price.append(symbol)
                else:
                    continue
            market.set_price(symbol, price)
        if not bars_by_symbol:
            return {"ok": False, "message": "所选标的没有历史行情，无法计算指标"}
        if not any(s in market.symbols() and self._prices.get(s) for s in bars_by_symbol) \
                and not is_sim:
            return {"ok": False, "message": "QMT 模式下没有实时价，已取消自动交易；"
                                            "本版不订阅行情，请先在实盘页刷新现价或手动下单"}
        strategy = self._ensure_strategy(key)
        context = StrategyContext(key, self._session_id, self._router,
                                  self._account, market,
                                  instrument_lookup=self.container.instruments.get)
        context.stamp = datetime.now()
        strategy.bind(context)
        today_bars = {symbol: market.latest(symbol)
                      for symbol in bars_by_symbol if market.latest(symbol) is not None}
        context.begin_bar(datetime.now())
        strategy.on_bars(today_bars, datetime.now())
        self._emit(AUTO_TRADE_TRIGGERED, {
            "session_id": self._session_id, "strategy": key,
            "symbols": list(today_bars), "forced": bool(force),
        })
        self._audit("live", "auto_trade", "策略 %s，标的 %s"
                    % (key, ",".join(today_bars)))
        return {"ok": True, "strategy": key, "symbols": list(today_bars),
                "fallback_prices": missing_price}

    # ---------------------------------------------------------- 网关事件

    def _on_gateway(self, event: str, data: Any) -> None:
        try:
            if event == "status":
                self._on_status(data)
            elif event == "account":
                self._on_account(data)
            elif event == "position":
                self._on_position(data)
            elif event == "order":
                self._on_order(data)
            elif event == "trade":
                self._on_trade(data)
            elif event == "tick":
                self._on_tick(data)
        except Exception:  # noqa: BLE001 - 回调异常不能拖垮网关线程
            LOGGER.exception("处理网关事件失败：%s", event)

    def _on_status(self, data: Dict[str, Any]) -> None:
        status = str((data or {}).get("status", "")).lower()
        if status == GW_CONNECTED:
            self._status = "connected"
            self._emit(GATEWAY_CONNECTED, dict(data or {}))
        elif status in ("disconnected", "error"):
            self._status = "disconnected"
            changed = self._oms.mark_unknown("连接断开，状态未知")
            self._persist_orders(changed)
            self._emit(GATEWAY_DISCONNECTED, dict(data or {}))
        elif status:
            self._status = status

    def _on_account(self, update: AccountUpdate) -> None:
        self._account_update = update
        self._sync_account()
        self._emit(ACCOUNT_UPDATED, asdict(update))

    def _on_position(self, update: PositionUpdate) -> None:
        with self._lock:
            if update.qty <= 0:
                self._positions.pop(update.symbol, None)
            else:
                self._positions[update.symbol] = update
        self._sync_account()
        self._emit(POSITION_UPDATED, asdict(update))

    def _on_order(self, update: OrderUpdate) -> None:
        order = self._oms.apply_update(update)
        if order is None:
            return
        self._broker_remember(order)
        self._persist_orders([order])
        self._emit(ORDER_UPDATED, self._order_payload(order))
        if order.status in ("FILLED", "CANCELLED", "REJECTED"):
            self._audit("live", "order_" + order.status.lower(),
                        "%s %s %s %d/%d" % (order.order_id, order.symbol,
                                            order.side, order.filled_qty, order.qty))

    def _on_trade(self, trade: TradeUpdate) -> None:
        fill = self._oms.apply_trade(trade)
        if fill is None:
            return
        try:
            self.container.repos.fills.save_many([fill])
        except Exception:  # noqa: BLE001 - 外部单可能没有本地 order 行
            LOGGER.debug("成交落库失败：%s", fill.fill_id)
        self._emit(ORDER_FILLED, {
            "fill_id": fill.fill_id, "order_id": fill.order_id,
            "trade_id": fill.trade_id, "symbol": fill.symbol, "side": fill.side,
            "qty": fill.qty, "price": fill.price, "fees": fill.commission,
            "time": fill.dt,
        })
        order = self._oms.get(fill.order_id) or self._oms.by_broker(trade.broker_order_id)
        if order is not None:
            self._broker_remember(order)
            self._persist_orders([order])

    def _on_tick(self, tick: TickUpdate) -> None:
        if tick.last_price and tick.last_price > 0:
            with self._lock:
                self._prices[tick.symbol] = float(tick.last_price)

    # ---------------------------------------------------------- 同步线程

    def _sync_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sync_once()
            except Exception:  # noqa: BLE001
                LOGGER.exception("实盘同步失败")
            try:
                self._maybe_auto_trade()
            except Exception:  # noqa: BLE001
                LOGGER.exception("自动交易调度失败")
            self._stop_event.wait(3.0)

    def sync_now(self) -> None:
        """页面轮询用：立即向柜台拉一次账户、持仓与委托。"""
        if self._gateway is None:
            return
        self._sync_once()

    def _sync_once(self) -> None:
        gateway = self._gateway
        if gateway is None or not gateway.is_connected:
            return
        try:
            account = gateway.query_account()
            if account is not None:
                self._on_account(account)
            for position in gateway.query_positions() or []:
                self._on_position(position)
            remote = gateway.query_orders() or []
            issues = self._oms.reconcile(remote)
            for issue in issues:
                LOGGER.info("对账：%s", issue)
            if issues:
                self._persist_orders(self._oms.all())
        except GatewayError as exc:
            self._last_error = str(exc)
            self._emit(GATEWAY_ERROR, {"message": str(exc)})
        self._last_sync = datetime.now()
        router = self._router
        if router is not None:
            router.risk.on_snapshot(self._account.snapshot(datetime.now()))

    def _maybe_auto_trade(self) -> None:
        if not self._auto_trade or not self._armed or not self.running:
            return
        today = datetime.now().date()
        stamp = today.isoformat()
        if self._auto_done == stamp:
            return
        try:
            if not trading_calendar.is_trading_day(today):
                return
        except Exception:  # noqa: BLE001
            return
        if datetime.now().time() < self._run_time():
            return
        self._auto_done = stamp
        try:
            LOGGER.info("自动交易：%s", self.run_strategy_once(force=True))
        except Exception:  # noqa: BLE001
            LOGGER.exception("自动交易失败")
            self._audit("live", "auto_trade_failed", "自动交易异常", severity="ERROR")

    def _run_time(self) -> dtime:
        text = self.config.live.run_time or "14:55"
        try:
            hour, minute = text.split(":")[:2]
            return dtime(int(hour), int(minute))
        except (ValueError, TypeError):
            return dtime(14, 55)

    # ---------------------------------------------------------- 内部工具

    def _sync_account(self) -> None:
        update = self._account_update
        if update is None:
            return
        positions = [
            Position(symbol=p.symbol, qty=p.qty, available_qty=p.available_qty,
                     avg_cost=p.avg_cost or 0.0,
                     last_price=self._prices.get(p.symbol) or p.last_price or 0.0)
            for p in self._positions.values() if p.qty > 0
        ]
        equity = update.equity if update.equity > 0 else None
        self._account.sync_from_broker(
            cash=update.cash, positions=positions,
            frozen_cash=update.frozen_cash, equity=equity,
            dt=update.updated_at or datetime.now(),
        )

    def _order_payload(self, order: Order) -> Dict[str, Any]:
        return {
            "order_id": order.order_id, "client_id": order.client_id,
            "broker_order_id": order.broker_order_id, "symbol": order.symbol,
            "side": order.side, "qty": order.qty, "filled_qty": order.filled_qty,
            "price": order.limit_price, "avg_fill_price": order.avg_fill_price,
            "status": order.status, "reason": order.reason, "time": order.updated_at,
        }

    def _persist_orders(self, orders: List[Order]) -> None:
        rows = [o for o in orders if o is not None and not o.order_id.startswith("EXT-")]
        if not rows:
            return
        try:
            self.container.repos.orders.save_many(rows)
        except Exception:  # noqa: BLE001
            LOGGER.exception("委托落库失败")

    def _broker_remember(self, order: Order) -> None:
        if self._broker is not None:
            self._broker.remember(order)

    def _price_of(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def _load_bars(self, symbol: str, limit: int = 260):
        try:
            return self.container.repos.bars.get_bars(symbol, limit=limit)
        except Exception:  # noqa: BLE001
            LOGGER.exception("读取 %s 行情失败", symbol)
            return []

    def _default_symbols(self) -> List[str]:
        symbols = []
        for row in self.container.services.market.coverage():
            if row.get("symbol"):
                symbols.append(row["symbol"])
            if len(symbols) >= 3:
                break
        if symbols:
            return symbols
        return list(self.container.instruments.symbols())[:3]

    def _ensure_strategy(self, key: str):
        if self._strategy is None:
            self._strategy = self.container.strategies.create(
                key, self._strategy_params)
            self._strategy.on_start()
        return self._strategy

    def _in_session(self) -> bool:
        try:
            return trading_calendar.is_market_open(datetime.now())
        except Exception:  # noqa: BLE001
            return True

    def _today_order_count(self, day) -> int:
        counter = self._order_counter
        if counter is None or counter[0] != day:
            return 0
        return counter[1]

    def _bump_order_counter(self, day) -> None:
        counter = self._order_counter
        if counter is None or counter[0] != day:
            self._order_counter = [day, 1]
        else:
            counter[1] += 1

    def _next_client_id(self, symbol: str, side: str) -> str:
        self._client_seq += 1
        return "LT-%s-%03d-%s-%s" % (
            datetime.now().strftime("%H%M%S"), self._client_seq,
            symbol.replace(".", ""), side)

    def _emit(self, topic: str, payload: Dict[str, Any]) -> None:
        bus = self.container.bus
        if bus is None:
            return
        try:
            bus.emit(topic, **payload)
        except Exception:  # noqa: BLE001 - 事件失败不影响交易
            LOGGER.exception("发布事件失败：%s", topic)

    def _audit(self, category: str, action: str, detail: str,
               severity: str = "INFO") -> None:
        try:
            self.container.repos.audit.write(category=category, action=action,
                                            detail=detail, severity=severity)
        except Exception:  # noqa: BLE001
            LOGGER.exception("审计写入失败")

    def shutdown(self) -> None:
        try:
            if self._gateway is not None:
                self.stop_session("程序退出")
        except Exception:  # noqa: BLE001
            LOGGER.exception("关闭实盘服务失败")
