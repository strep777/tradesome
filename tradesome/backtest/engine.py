"""回测引擎：事件驱动的逐 bar 模拟。

成交规则（与文档一致）：
- 策略在第 t 根 bar 收盘后产生订单，订单最早在第 t+1 根 bar 成交；
- 市价单按下一根 bar 的开盘价成交，限价单需价格穿越；
- 涨跌停、停牌、成交量占比、T+1 都在 MatchingEngine 里处理。

引擎不依赖 UI，也不直接读写数据库；它只接收内存中的行情与策略，返回结果对象。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core.errors import BacktestError
from ..core.ids import new_order_id, new_run_id
from ..core.logging import get_logger
from ..core.money import round_money
from ..core.types import (
    ORDER_MARKET, ST_CANCELLED, ST_FILLED, ST_PARTIAL, ST_REJECTED,
    Bar, BacktestConfig, BacktestResult, Fill, Order, Position,
    TradeRecord,
)
from ..execution.router import OrderRouter
from ..portfolio.account import Account
from ..risk.manager import RiskManager
from ..risk.rules import RiskLimits
from ..strategy.base import Strategy
from ..strategy.context import StrategyContext
from .costs import apply_slippage, sell_cost
from .matching import MatchingEngine
from .metrics import compute_metrics, drawdown_curve, monthly_returns

LOGGER = get_logger("backtest.engine")

ProgressFn = Callable[[float, int, int], None]
CancelFn = Callable[[], bool]


class _Cancelled(Exception):
    """内部信号：用户取消回测。"""


# --------------------------------------------------------------------------- 行情视图


class BacktestMarketView:
    """给策略用的只读行情视图，保证策略看不到未来数据。"""

    def __init__(self, bars_by_symbol: Dict[str, Sequence[Bar]]) -> None:
        self.data = bars_by_symbol
        self.cursor: Dict[str, int] = {s: -1 for s in bars_by_symbol}
        self.current: Dict[str, Bar] = {}
        self._now: Optional[datetime] = None
        self._closes: Dict[str, List[float]] = {
            s: [b.close for b in bars] for s, bars in bars_by_symbol.items()
        }

    def advance(self, bar: Bar) -> None:
        self.cursor[bar.symbol] = self.cursor.get(bar.symbol, -1) + 1
        self.current[bar.symbol] = bar
        self._now = bar.dt

    def now(self) -> datetime:
        return self._now or datetime(2020, 1, 1)

    def latest(self, symbol: str) -> Optional[Bar]:
        return self.current.get(symbol)

    def history(self, symbol: str, n: Optional[int] = None,
                field: str = "close") -> List[float]:
        end = self.cursor.get(symbol, -1) + 1
        if end <= 0:
            return []
        start = 0 if not n or n <= 0 else max(0, end - int(n))
        if field == "close":
            return list(self._closes.get(symbol, [])[start:end])
        bars = self.data.get(symbol, ())
        return [float(getattr(b, field, getattr(b, "close", 0.0)))
                for b in bars[start:end]]

    def bars(self, symbol: str, n: Optional[int] = None) -> List[Bar]:
        end = self.cursor.get(symbol, -1) + 1
        start = 0 if not n or n <= 0 else max(0, end - int(n))
        return list(self.data.get(symbol, ())[start:end])


# --------------------------------------------------------------------------- 账户视图


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
    def positions(self) -> List[Position]:
        return self._account.position_list

    def position_of(self, symbol: str) -> Optional[Position]:
        return self._account.position_of(symbol)

    def available_qty(self, symbol: str) -> int:
        return self._account.available_qty(symbol)


# --------------------------------------------------------------------------- 模拟券商


@dataclass
class _TradeState:
    qty: int = 0
    cost: float = 0.0
    fees: float = 0.0
    opened_at: Optional[datetime] = None


class SimBroker:
    """回测用券商：只负责挂单与撮合，不判断风控。"""

    name = "simulated"

    def __init__(self, matcher: MatchingEngine, account: Account,
                 instruments) -> None:
        self.matcher = matcher
        self.account = account
        self.instruments = instruments
        self.pending: Dict[str, Order] = {}
        self.orders: Dict[str, Order] = {}
        self._order: List[str] = []

    # Broker 协议

    def submit(self, order: Order, price: float) -> Order:
        if order.side == "BUY":
            estimate = round_money(price * order.qty + 5.0)
            self.account.freeze(order.order_id, min(estimate, self.account.cash))
        self.orders[order.order_id] = order
        self.pending[order.order_id] = order
        self._order.append(order.order_id)
        return order

    def cancel(self, order_id: str) -> bool:
        order = self.pending.pop(order_id, None)
        if order is None:
            return False
        self.account.release(order_id)
        updated = order.evolve(status=ST_CANCELLED, updated_at=datetime.now())
        self.orders[order_id] = updated
        return True

    def open_orders(self) -> List[Order]:
        return list(self.pending.values())

    # 引擎内部

    def process_bar(self, bar: Bar, prev_close: Optional[float]
                    ) -> List[Tuple[Order, Fill]]:
        instrument = self.instruments.get(bar.symbol)
        if instrument is None:
            return []
        results: List[Tuple[Order, Fill]] = []
        for order_id in list(self.pending.keys()):
            order = self.pending.get(order_id)
            if order is None or order.symbol != bar.symbol:
                continue
            outcome = self.matcher.match(
                order, bar, instrument, self.account.cash,
                self.account.available_qty(bar.symbol), prev_close=prev_close)
            if outcome.rejected:
                self.pending.pop(order_id, None)
                self.account.release(order_id)
                self.orders[order_id] = order.evolve(
                    status=ST_REJECTED, updated_at=bar.dt,
                    reason=outcome.reason or "撮合失败")
                continue
            if outcome.fill is None:
                continue
            fill = outcome.fill
            self.account.release(order_id)
            self.account.apply_fill(fill)
            remain = int(order.remaining) - fill.qty
            status = ST_FILLED if remain <= 0 else ST_PARTIAL
            filled = int(order.filled_qty) + fill.qty
            avg = ((order.avg_fill_price * order.filled_qty + fill.price * fill.qty)
                   / filled) if filled else fill.price
            updated = order.evolve(status=status, filled_qty=filled,
                                   avg_fill_price=avg, updated_at=bar.dt)
            self.orders[order.order_id] = updated
            if remain <= 0:
                self.pending.pop(order_id, None)
            else:
                self.pending[order_id] = updated
            results.append((updated, fill))
        return results

    def all_orders(self) -> List[Order]:
        return [self.orders[oid] for oid in self._order if oid in self.orders]


# --------------------------------------------------------------------------- 成交记录


class TradeBook:
    """把成交配对成一次完整的买卖交易，用于胜率、盈亏比等指标。"""

    def __init__(self) -> None:
        self.state: Dict[str, _TradeState] = {}
        self.closed: List[TradeRecord] = []

    def on_fill(self, fill: Fill, reason: str = "") -> Optional[TradeRecord]:
        state = self.state.setdefault(fill.symbol, _TradeState())
        if fill.side == "BUY":
            state.qty += fill.qty
            state.cost += fill.gross_amount + fill.fees
            state.fees += fill.fees
            state.opened_at = state.opened_at or fill.dt
            return None

        if state.qty <= 0:
            return None
        qty = min(fill.qty, state.qty)
        unit_cost = state.cost / state.qty if state.qty else 0.0
        cost_out = unit_cost * qty
        buy_fee = state.fees * (qty / state.qty) if state.qty else 0.0
        pnl = round_money(fill.net_amount - cost_out)
        record = TradeRecord(
            symbol=fill.symbol, open_dt=state.opened_at or fill.dt, close_dt=fill.dt,
            qty=qty, entry_price=round_money(unit_cost / 1.0),
            exit_price=fill.price, pnl=pnl,
            pnl_pct=(pnl / cost_out) if cost_out > 0 else 0.0,
            fees=round_money(buy_fee + fill.fees),
            holding_days=max(0, (fill.dt.date() - (state.opened_at or fill.dt).date()).days),
            exit_reason=reason or "策略卖出",
        )
        state.qty -= qty
        state.cost -= cost_out
        state.fees -= buy_fee
        if state.qty <= 0:
            state.qty = 0
            state.cost = 0.0
            state.fees = 0.0
            state.opened_at = None
        self.closed.append(record)
        return record


# --------------------------------------------------------------------------- 引擎


class BacktestEngine:
    def __init__(self, config: BacktestConfig,
                 bars_by_symbol: Dict[str, Sequence[Bar]],
                 strategy: Strategy,
                 instruments,
                 fee=None,
                 bus=None,
                 risk_limits: Optional[RiskLimits] = None) -> None:
        if strategy is None:
            raise BacktestError("回测需要先选择策略")
        self.config = config
        self.bars = {s: sorted(list(b), key=lambda b: b.dt)
                     for s, b in bars_by_symbol.items() if b}
        self.strategy = strategy
        self.instruments = instruments
        self.fee = fee
        self.bus = bus
        self.risk_limits = risk_limits or RiskLimits()
        self._lock = threading.RLock()
        self._log: List[str] = []

    # ---------------------------------------------------------------- 主流程

    def run(self, progress: Optional[ProgressFn] = None,
            cancel: Optional[CancelFn] = None,
            run_id: Optional[str] = None) -> BacktestResult:
        started = datetime.now()
        clock = time.monotonic()
        run_id = run_id or new_run_id(started)
        self._log = []
        self._prepare()

        timeline = self._timeline()
        if not timeline:
            raise BacktestError("所选时间区间内没有行情数据，请先在数据中心生成或导入数据")

        total = len(timeline)
        fee = self.fee or self.config.fee
        matcher = MatchingEngine(fee, self.config.max_volume_ratio)
        account = Account(self.config.initial_cash, t_plus=1)
        broker = SimBroker(matcher, account, self.instruments)
        risk = RiskManager(limits=self._effective_limits())

        market = BacktestMarketView(self.bars)
        book = TradeBook()
        # 回测与实盘共用同一个下单入口，风控、T+1、幂等规则完全一致
        router = OrderRouter(
            account=account, broker=broker, risk=risk, instruments=self.instruments,
            price_lookup=lambda symbol: (
                market.latest(symbol).close if market.latest(symbol) else None))
        ctx = StrategyContext(
            strategy_id=self.config.strategy_key or "backtest",
            session_id=run_id, gateway=router, account=_AccountView(account),
            market=market, instrument_lookup=self.instruments.get)
        self.strategy.bind(ctx)
        self.strategy.configure(self.config.strategy_params or {})
        self.strategy.on_start()

        curve: List[Tuple[datetime, float]] = []
        exposure: List[float] = []
        prices: Dict[str, float] = {}
        day: Optional[date] = None
        liquidated_day: Optional[date] = None
        cancelled = False
        last_progress = 0.0

        try:
            for index, moment in enumerate(timeline):
                today = moment.date()
                if day != today:
                    account.settle_day(moment)
                    risk.reset_day()
                    day = today
                    liquidated_day = None

                today_bars: Dict[str, Bar] = {}
                for symbol in self.config.symbols:
                    bar = self._bar_at(symbol, moment)
                    if bar is None:
                        continue
                    today_bars[symbol] = bar
                    market.advance(bar)
                    prev_close = self._prev_close(symbol, moment)
                    for order, fill in broker.process_bar(bar, prev_close):
                        record = book.on_fill(fill, order.reason)
                        if record is not None:
                            self._event(record)
                    prices[symbol] = bar.close

                if not today_bars:
                    continue

                account.mark_to_market(prices, moment)
                snapshot = account.snapshot(moment)
                risk.on_snapshot(snapshot)

                if (account.day_pnl_pct <= -abs(risk.limits.daily_loss_close)
                        and liquidated_day != today and account.position_list):
                    self._force_close(account, broker, book, today_bars, moment, prices)
                    snapshot = account.snapshot(moment)
                    liquidated_day = today
                    self._log_line("风控触发：当日亏损 %.2f%%，已强制平仓"
                                   % (account.day_pnl_pct * 100))

                if risk.kill_switch:
                    self._log_line("风控熔断：%s" % (risk.kill_reason or "组合回撤超限"))

                curve.append((moment, account.equity))
                exposure.append(account.position_value / account.equity
                                if account.equity > 0 else 0.0)

                ctx.begin_bar(moment)
                try:
                    self.strategy.on_bars(today_bars, moment)
                except Exception as exc:  # noqa: BLE001 - 策略异常终止回测但保留结果
                    self._log_line("策略执行异常：%s" % exc)
                    LOGGER.exception("策略执行异常")
                    break

                if cancel is not None and index % 25 == 0 and cancel():
                    raise _Cancelled()
                now = time.monotonic()
                if progress is not None and (index % 50 == 0 or now - last_progress > 0.1
                                             or index == total - 1):
                    last_progress = now
                    progress(round((index + 1) * 100.0 / total, 1), index + 1, total)
        except _Cancelled:
            cancelled = True
            self._log_line("回测已被用户取消")
        finally:
            self.strategy.on_stop()

        # 收尾：最后一天也做一次结算
        if curve:
            account.mark_to_market(prices, curve[-1][0])
            curve[-1] = (curve[-1][0], account.equity)

        benchmark = self._benchmark_curve(timeline[0], timeline[-1])
        metrics = compute_metrics(curve, book.closed, self.config.initial_cash,
                                  benchmark_curve=benchmark, exposure_series=exposure)
        result = BacktestResult(
            run_id=run_id, config=self.config, metrics=metrics,
            equity_curve=curve,
            drawdown_curve=list(zip([d for d, _ in curve],
                                    drawdown_curve([v for _, v in curve]))),
            daily_returns=self._daily_returns(curve),
            monthly_returns=monthly_returns(curve),
            trades=list(book.closed),
            orders=broker.all_orders(),
            logs=list(self._log) + list(self.strategy.logs),
            started_at=started, finished_at=datetime.now(),
            duration_sec=round(time.monotonic() - clock, 3),
            cancelled=cancelled, benchmark_curve=benchmark,
        )
        return result

    # ---------------------------------------------------------------- 数据准备

    def _prepare(self) -> None:
        if not self.config.symbols:
            raise BacktestError("请至少选择一个标的")
        allowed = set(self.config.symbols)
        self.bars = {s: b for s, b in self.bars.items() if s in allowed}
        for symbol, bars in self.bars.items():
            self.bars[symbol] = [b for b in bars if self.config.start <= b.dt.date()
                                 <= self.config.end]
        missing = [s for s in allowed if not self.bars.get(s)]
        if missing:
            raise BacktestError("以下标的缺少行情数据：%s" % "、".join(missing))

    def _timeline(self) -> List[datetime]:
        stamps = set()
        for bars in self.bars.values():
            stamps.update(b.dt for b in bars)
        return sorted(stamps)

    def _bar_at(self, symbol: str, moment: datetime) -> Optional[Bar]:
        bars = self.bars.get(symbol) or []
        # 二分查找：bar 数多时也够快
        low, high = 0, len(bars) - 1
        while low <= high:
            mid = (low + high) // 2
            if bars[mid].dt == moment:
                return bars[mid]
            if bars[mid].dt < moment:
                low = mid + 1
            else:
                high = mid - 1
        return None

    def _prev_close(self, symbol: str, moment: datetime) -> Optional[float]:
        bars = self.bars.get(symbol) or []
        low, high = 0, len(bars) - 1
        found = None
        while low <= high:
            mid = (low + high) // 2
            if bars[mid].dt < moment:
                found = bars[mid]
                low = mid + 1
            else:
                high = mid - 1
        return found.close if found is not None else None

    def _benchmark_curve(self, start: datetime,
                         end: datetime) -> Optional[List[Tuple[datetime, float]]]:
        symbol = self.config.benchmark
        bars = self.bars.get(symbol)
        if not bars:
            return None
        rows = [b for b in bars if start <= b.dt <= end]
        if len(rows) < 2:
            return None
        base = rows[0].close
        if base <= 0:
            return None
        return [(b.dt, self.config.initial_cash * b.close / base) for b in rows]

    @staticmethod
    def _daily_returns(curve: List[Tuple[datetime, float]]) -> List[Tuple[datetime, float]]:
        out: List[Tuple[datetime, float]] = []
        previous = None
        for moment, value in curve:
            if previous and previous > 0:
                out.append((moment, value / previous - 1.0))
            previous = value
        return out

    # ---------------------------------------------------------------- 强制平仓

    def _force_close(self, account: Account, broker: SimBroker, book: TradeBook,
                     bars: Dict[str, Bar], moment: datetime,
                     prices: Dict[str, float]) -> None:
        fee = self.fee or self.config.fee
        for position in account.position_list:
            bar = bars.get(position.symbol)
            price = prices.get(position.symbol) or (bar.close if bar else position.last_price)
            if not price or price <= 0:
                continue
            instrument = self.instruments.get(position.symbol)
            if instrument is None:
                continue
            deal = apply_slippage(price, "SELL", fee, instrument.tick_size)
            amount = round_money(deal * position.qty)
            total_fee, stamp, commission = sell_cost(amount, fee)
            transfer = round_money(amount * fee.transfer_fee_rate)
            fill = Fill(
                fill_id=new_order_id(moment), order_id="RISK-" + position.symbol,
                symbol=position.symbol, side="SELL", qty=position.qty, price=deal,
                dt=moment, commission=commission, stamp_tax=stamp,
                transfer_fee=transfer, slippage_cost=round_money(
                    abs(deal - price) * position.qty))
            account.apply_fill(fill)
            book.on_fill(fill, "风控强制平仓")
            order = Order(order_id="RISK-" + position.symbol, client_id="",
                          symbol=position.symbol, side="SELL", qty=position.qty,
                          order_type=ORDER_MARKET, status=ST_FILLED,
                          filled_qty=position.qty, avg_fill_price=deal,
                          created_at=moment, updated_at=moment,
                          reason="风控强制平仓")
            broker.orders[order.order_id] = order

    def _effective_limits(self) -> RiskLimits:
        if self.config.risk_enabled:
            return self.risk_limits
        # 关闭风控时用极宽松的上限，保留撮合与记账行为一致
        return RiskLimits(single_trade_pct=1.0, single_symbol_pct=1.0,
                          max_gross_exposure=1.0, daily_loss_halt=1.0,
                          daily_loss_close=1.0, max_drawdown_reduce=1.0,
                          max_drawdown_halt=1.0, max_orders_per_minute=100000)

    def _event(self, record: TradeRecord) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit("backtest.trade", trade=record)
        except Exception:  # noqa: BLE001 - 事件失败不影响回测
            LOGGER.exception("发布回测成交事件失败")

    def _log_line(self, message: str) -> None:
        self._log.append("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), message))


def run_backtest(config: BacktestConfig, bars_by_symbol: Dict[str, Sequence[Bar]],
                 strategy: Strategy, instruments, fee=None,
                 progress: Optional[ProgressFn] = None,
                 cancel: Optional[CancelFn] = None,
                 bus=None,
                 risk_limits: Optional[RiskLimits] = None) -> BacktestResult:
    engine = BacktestEngine(config, bars_by_symbol, strategy, instruments,
                            fee=fee, bus=bus, risk_limits=risk_limits)
    return engine.run(progress=progress, cancel=cancel)
