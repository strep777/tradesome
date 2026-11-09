"""各实体仓储：把 SQL 关在本文件内，上层只见领域对象。

约定：所有时间以 'YYYY-MM-DD HH:MM:SS' 文本存储（SQLite 下可按字符串排序，
天然支持区间查询），读取时还原为 datetime。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ..core.errors import PersistenceError
from ..core.ids import new_run_id
from ..core.money import mean, stdev
from ..core.types import (
    BacktestConfig, BacktestResult, Bar, FeeProfile, Fill, Instrument,
    Order, Position, TradeRecord,
)
from .db import Database
from .live_events import LiveEventRepository
from .strategy_profiles import StrategyProfileRepository

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------- 工具


def dt_text(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime(_TIME_FMT)


def text_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text, _TIME_FMT)
    except ValueError:
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None


def _date_bound(value, end: bool) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return dt_text(value)
    if isinstance(value, date):
        return "%s %s" % (value.isoformat(), "23:59:59" if end else "00:00:00")
    return str(value)


def _loads(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _BaseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _to_dict(row) -> Dict[str, Any]:
        return dict(row)


# ---------------------------------------------------------------- 标的


class InstrumentRepository(_BaseRepository):
    def upsert(self, item: Instrument) -> None:
        self.upsert_many([item])

    def upsert_many(self, items: Sequence[Instrument]) -> int:
        if not items:
            return 0
        now = dt_text(datetime.now())
        rows = [
            (i.symbol, i.name, i.kind, i.lot_size, i.price_limit_pct, i.t_plus,
             i.tick_size, i.board, _dumps(i.fee.to_dict()), now)
            for i in items
        ]
        self.db.executemany(
            """
            INSERT INTO instruments(symbol, name, kind, lot_size, price_limit_pct,
                                    t_plus, tick_size, board, fee_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, lot_size=excluded.lot_size,
                price_limit_pct=excluded.price_limit_pct, t_plus=excluded.t_plus,
                tick_size=excluded.tick_size, board=excluded.board,
                fee_json=excluded.fee_json, updated_at=excluded.updated_at
            """,
            rows,
        )
        return len(rows)

    def get(self, symbol: str) -> Optional[Instrument]:
        row = self.db.query_one("SELECT * FROM instruments WHERE symbol=?", (symbol,))
        return self._to_obj(row) if row else None

    def list_all(self) -> List[Instrument]:
        rows = self.db.query("SELECT * FROM instruments ORDER BY symbol")
        return [self._to_obj(r) for r in rows]

    def delete(self, symbol: str) -> bool:
        cur = self.db.execute("DELETE FROM instruments WHERE symbol=?", (symbol,))
        return cur.rowcount > 0

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM instruments", default=0) or 0)

    @staticmethod
    def _to_obj(row) -> Instrument:
        return Instrument(
            symbol=row["symbol"],
            name=row["name"] or row["symbol"],
            kind=row["kind"],
            lot_size=int(row["lot_size"]),
            price_limit_pct=float(row["price_limit_pct"]),
            t_plus=int(row["t_plus"]),
            tick_size=float(row["tick_size"]),
            fee=FeeProfile.from_dict(_loads(row["fee_json"], {})),
            board=row["board"],
        )


# ---------------------------------------------------------------- 行情


class BarRepository(_BaseRepository):
    """K 线存储。批量写入必须走事务，单标的十万行约 1 秒。"""

    INSERT = """
        INSERT INTO bars(symbol, dt, timeframe, open, high, low, close, volume, amount)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol, dt, timeframe) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, amount=excluded.amount
    """

    def upsert_bars(self, bars: Sequence[Bar], batch: int = 5000) -> int:
        if not bars:
            return 0
        total = 0
        batch = max(100, int(batch))
        for start in range(0, len(bars), batch):
            chunk = bars[start:start + batch]
            rows = [
                (b.symbol, dt_text(b.dt), b.timeframe, b.open, b.high, b.low,
                 b.close, b.volume, b.amount)
                for b in chunk
            ]
            # 分批提交，既控制内存也缩短单次写锁持有时间
            if len(bars) > batch:
                self.db.executemany(self.INSERT, rows)
            else:
                with self.db.transaction() as conn:
                    conn.executemany(self.INSERT, rows)
            total += len(rows)
        return total

    def get_bars(self, symbol: str, timeframe: str = "1d",
                 start: Union[date, datetime, None] = None,
                 end: Union[date, datetime, None] = None,
                 limit: Optional[int] = None) -> List[Bar]:
        sql = ["SELECT * FROM bars WHERE symbol=? AND timeframe=?"]
        params: List[Any] = [symbol, timeframe]
        start_text = _date_bound(start, end=False)
        if start_text:
            sql.append("AND dt >= ?")
            params.append(start_text)
        end_text = _date_bound(end, end=True)
        if end_text:
            sql.append("AND dt <= ?")
            params.append(end_text)
        sql.append("ORDER BY dt ASC")
        if limit:
            sql.append("LIMIT ?")
            params.append(int(limit))
        rows = self.db.query(" ".join(sql), params)
        return [self._to_bar(r) for r in rows]

    def get_bars_many(self, symbols: Sequence[str], timeframe: str = "1d",
                      start: Union[date, datetime, None] = None,
                      end: Union[date, datetime, None] = None) -> Dict[str, List[Bar]]:
        return {s: self.get_bars(s, timeframe, start, end) for s in symbols}

    def coverage(self, symbol: str, timeframe: str = "1d") -> Tuple[int, Optional[datetime], Optional[datetime]]:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, MIN(dt) AS first, MAX(dt) AS last "
            "FROM bars WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        )
        if not row:
            return 0, None, None
        return int(row["n"] or 0), text_dt(row["first"]), text_dt(row["last"])

    def last_dt(self, symbol: str, timeframe: str = "1d") -> Optional[datetime]:
        return self.coverage(symbol, timeframe)[2]

    def count(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM bars WHERE 1=1"
        params: List[Any] = []
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        if timeframe:
            sql += " AND timeframe=?"
            params.append(timeframe)
        return int(self.db.scalar(sql, params, default=0) or 0)

    def symbols(self, timeframe: str = "1d") -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT symbol, COUNT(*) AS n, MIN(dt) AS first, MAX(dt) AS last "
            "FROM bars WHERE timeframe=? GROUP BY symbol ORDER BY symbol",
            (timeframe,),
        )
        return [
            {
                "symbol": r["symbol"],
                "count": int(r["n"]),
                "first": text_dt(r["first"]),
                "last": text_dt(r["last"]),
            }
            for r in rows
        ]

    def delete_symbol(self, symbol: str, timeframe: Optional[str] = None) -> int:
        if timeframe:
            cur = self.db.execute(
                "DELETE FROM bars WHERE symbol=? AND timeframe=?", (symbol, timeframe))
        else:
            cur = self.db.execute("DELETE FROM bars WHERE symbol=?", (symbol,))
        return int(cur.rowcount or 0)

    def delete_timeframe(self, timeframe: str) -> int:
        cur = self.db.execute("DELETE FROM bars WHERE timeframe=?", (timeframe,))
        return int(cur.rowcount or 0)

    def prune_before(self, moment: datetime, symbol: Optional[str] = None) -> int:
        sql = "DELETE FROM bars WHERE dt < ?"
        params: List[Any] = [dt_text(moment)]
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        return int(self.db.execute(sql, params).rowcount or 0)

    @staticmethod
    def _to_bar(row) -> Bar:
        return Bar(
            symbol=row["symbol"],
            dt=text_dt(row["dt"]) or datetime(1970, 1, 1),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            amount=float(row["amount"]),
            timeframe=row["timeframe"],
        )


# ---------------------------------------------------------------- 回测


class BacktestRepository(_BaseRepository):
    def save(self, result: BacktestResult) -> str:
        config = result.config
        run_id = result.run_id or new_run_id()
        created = dt_text(result.started_at or datetime.now())
        equity = [[dt.strftime(_TIME_FMT), round(v, 4)] for dt, v in (result.equity_curve or [])]
        drawdown = [[dt.strftime(_TIME_FMT), round(v, 6)] for dt, v in (result.drawdown_curve or [])]
        benchmark = [[dt.strftime(_TIME_FMT), round(v, 4)]
                     for dt, v in (result.benchmark_curve or [])]
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO backtest_runs(run_id, created_at, strategy_key, strategy_name,
                    start_date, end_date, initial_cash, metrics_json, config_json,
                    equity_json, drawdown_json, monthly_json, benchmark_json,
                    duration_sec, cancelled)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    metrics_json=excluded.metrics_json,
                    equity_json=excluded.equity_json,
                    drawdown_json=excluded.drawdown_json,
                    monthly_json=excluded.monthly_json,
                    benchmark_json=excluded.benchmark_json,
                    duration_sec=excluded.duration_sec,
                    cancelled=excluded.cancelled
                """,
                (
                    run_id, created, config.strategy_key, config.strategy_key,
                    config.start.isoformat(), config.end.isoformat(), config.initial_cash,
                    _dumps(result.metrics), _dumps(config.to_dict()), _dumps(equity),
                    _dumps(drawdown), _dumps(result.monthly_returns), _dumps(benchmark),
                    float(result.duration_sec), 1 if result.cancelled else 0,
                ),
            )
            conn.execute("DELETE FROM backtest_trades WHERE run_id=?", (run_id,))
            rows = [
                (run_id, seq, t.symbol, dt_text(t.open_dt), dt_text(t.close_dt), t.qty,
                 t.entry_price, t.exit_price, t.pnl, t.pnl_pct, t.fees,
                 t.holding_days, t.exit_reason)
                for seq, t in enumerate(result.trades)
            ]
            if rows:
                conn.executemany(
                    """
                    INSERT INTO backtest_trades(run_id, seq, symbol, open_dt, close_dt,
                        qty, entry_price, exit_price, pnl, pnl_pct, fees, holding_days,
                        exit_reason)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    rows,
                )
        return run_id

    def list_runs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT run_id, created_at, strategy_key, start_date, end_date, "
            "initial_cash, metrics_json, duration_sec, cancelled "
            "FROM backtest_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (max(1, int(limit)), max(0, int(offset))),
        )
        result = []
        for row in rows:
            metrics = _loads(row["metrics_json"], {})
            result.append({
                "run_id": row["run_id"],
                "created_at": text_dt(row["created_at"]),
                "strategy_key": row["strategy_key"],
                "start": row["start_date"],
                "end": row["end_date"],
                "initial_cash": float(row["initial_cash"] or 0),
                "total_return": metrics.get("total_return"),
                "sharpe": metrics.get("sharpe"),
                "max_drawdown": metrics.get("max_drawdown"),
                "trade_count": metrics.get("trade_count"),
                "duration_sec": float(row["duration_sec"] or 0),
                "cancelled": bool(row["cancelled"]),
            })
        return result

    def load(self, run_id: str) -> Optional[BacktestResult]:
        row = self.db.query_one("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,))
        if not row:
            return None
        config_data = _loads(row["config_json"], {})
        try:
            config = BacktestConfig.from_dict(config_data)
        except Exception:  # noqa: BLE001 - 旧记录缺字段时降级为最小可用配置
            config = BacktestConfig(
                symbols=(), start=date(2000, 1, 1), end=date(2000, 1, 1))
        equity = tuple((text_dt(dt) or datetime(1970, 1, 1), float(v))
                       for dt, v in _loads(row["equity_json"], []))
        drawdown = tuple((text_dt(dt) or datetime(1970, 1, 1), float(v))
                         for dt, v in _loads(row["drawdown_json"], []))
        benchmark = tuple((text_dt(dt) or datetime(1970, 1, 1), float(v))
                          for dt, v in _loads(row["benchmark_json"], []))
        daily_returns = _daily_returns(equity)
        result = BacktestResult(
            run_id=row["run_id"],
            config=config,
            metrics=_loads(row["metrics_json"], {}),
            equity_curve=equity,
            drawdown_curve=drawdown,
            daily_returns=daily_returns,
            monthly_returns=_loads(row["monthly_json"], {}),
            trades=tuple(self._trades(run_id)),
            orders=(),
            started_at=text_dt(row["created_at"]),
            finished_at=None,
            duration_sec=float(row["duration_sec"] or 0),
            cancelled=bool(row["cancelled"]),
            benchmark_curve=benchmark,
        )
        return result

    def _trades(self, run_id: str) -> List[TradeRecord]:
        rows = self.db.query(
            "SELECT * FROM backtest_trades WHERE run_id=? ORDER BY seq", (run_id,))
        return [
            TradeRecord(
                symbol=r["symbol"], open_dt=text_dt(r["open_dt"]),
                close_dt=text_dt(r["close_dt"]), qty=int(r["qty"]),
                entry_price=float(r["entry_price"]), exit_price=float(r["exit_price"]),
                pnl=float(r["pnl"]), pnl_pct=float(r["pnl_pct"]),
                fees=float(r["fees"]), holding_days=int(r["holding_days"]),
                exit_reason=r["exit_reason"] or "",
            )
            for r in rows
        ]

    def trades(self, run_id: str) -> List[TradeRecord]:
        return self._trades(run_id)

    def delete(self, run_id: str) -> bool:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM backtest_trades WHERE run_id=?", (run_id,))
            cur = conn.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
        return bool(cur.rowcount)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM backtest_runs", default=0) or 0)

    def clear(self) -> int:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM backtest_trades")
            cur = conn.execute("DELETE FROM backtest_runs")
        return int(cur.rowcount or 0)


def _daily_returns(equity: Sequence[Tuple[datetime, float]]) -> Tuple[Tuple[datetime, float], ...]:
    out: List[Tuple[datetime, float]] = []
    for i in range(1, len(equity)):
        prev = equity[i - 1][1]
        if prev > 0:
            out.append((equity[i][0], equity[i][1] / prev - 1.0))
    return tuple(out)


# ---------------------------------------------------------------- 会话


class SessionRepository(_BaseRepository):
    def create(self, session_id: str, strategy_key: str, strategy_params: Dict[str, Any],
               symbols: Sequence[str], initial_cash: float, speed: float = 1.0,
               started_at: Optional[datetime] = None, status: str = "RUNNING",
               note: str = "", mode: str = "paper", gateway: str = "") -> str:
        self.db.execute(
            """
            INSERT INTO sessions(session_id, strategy_key, strategy_params_json,
                symbols_json, started_at, stopped_at, status, initial_cash,
                final_equity, speed, note, mode, gateway)
            VALUES (?,?,?,?,?,NULL,?,?,0,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                status=excluded.status, speed=excluded.speed, mode=excluded.mode,
                gateway=excluded.gateway
            """,
            (session_id, strategy_key, _dumps(strategy_params), _dumps(list(symbols)),
             dt_text(started_at or datetime.now()), status, initial_cash, speed, note,
             mode, gateway),
        )
        return session_id

    def finish(self, session_id: str, final_equity: float, status: str = "STOPPED",
               stopped_at: Optional[datetime] = None) -> None:
        self.db.execute(
            "UPDATE sessions SET status=?, final_equity=?, stopped_at=? WHERE session_id=?",
            (status, float(final_equity), dt_text(stopped_at or datetime.now()), session_id),
        )

    def update_status(self, session_id: str, status: str) -> None:
        self.db.execute("UPDATE sessions SET status=? WHERE session_id=?", (status, session_id))

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM sessions WHERE session_id=?", (session_id,))
        return self._to_dict(row) if row else None

    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (max(1, int(limit)),))
        return [self._to_dict(r) for r in rows]

    def last(self) -> Optional[Dict[str, Any]]:
        rows = self.list_recent(1)
        return rows[0] if rows else None

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM sessions", default=0) or 0)

    def clear(self) -> int:
        return int(self.db.execute("DELETE FROM sessions").rowcount or 0)

    @staticmethod
    def _to_dict(row) -> Dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "strategy_key": row["strategy_key"],
            "strategy_params": _loads(row["strategy_params_json"], {}),
            "symbols": _loads(row["symbols_json"], []),
            "started_at": text_dt(row["started_at"]),
            "stopped_at": text_dt(row["stopped_at"]),
            "status": row["status"],
            "initial_cash": float(row["initial_cash"] or 0),
            "final_equity": float(row["final_equity"] or 0),
            "speed": float(row["speed"] or 1.0),
            "note": row["note"] or "",
            "mode": row["mode"] if "mode" in row.keys() else "paper",
            "gateway": row["gateway"] if "gateway" in row.keys() else "",
        }


# ---------------------------------------------------------------- 订单与成交


class OrderRepository(_BaseRepository):
    def save(self, order: Order) -> None:
        self.save_many([order])

    def save_many(self, orders: Sequence[Order]) -> int:
        if not orders:
            return 0
        rows = [
            (o.order_id, o.client_id or None, o.session_id, o.strategy_id, o.symbol,
             o.side, o.qty, o.order_type, o.limit_price, o.status, o.filled_qty,
             o.avg_fill_price, dt_text(o.created_at), dt_text(o.updated_at), o.reason,
             o.gateway, o.broker_order_id)
            for o in orders
        ]
        self.db.executemany(
            """
            INSERT INTO orders(order_id, client_id, session_id, strategy_id, symbol,
                side, qty, order_type, limit_price, status, filled_qty, avg_fill_price,
                created_at, updated_at, reason, gateway, broker_order_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET
                status=excluded.status, filled_qty=excluded.filled_qty,
                avg_fill_price=excluded.avg_fill_price, updated_at=excluded.updated_at,
                reason=excluded.reason, gateway=excluded.gateway,
                broker_order_id=excluded.broker_order_id
            """,
            rows,
        )
        return len(rows)

    def get(self, order_id: str) -> Optional[Order]:
        row = self.db.query_one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        return self._to_obj(row) if row else None

    def by_client_id(self, client_id: str) -> Optional[Order]:
        if not client_id:
            return None
        row = self.db.query_one("SELECT * FROM orders WHERE client_id=?", (client_id,))
        return self._to_obj(row) if row else None

    def list_by_session(self, session_id: str, limit: int = 500) -> List[Order]:
        rows = self.db.query(
            "SELECT * FROM orders WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
            (session_id, int(limit)))
        return [self._to_obj(r) for r in rows]

    def list_recent(self, limit: int = 100) -> List[Order]:
        rows = self.db.query(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [self._to_obj(r) for r in rows]

    def count(self, session_id: Optional[str] = None) -> int:
        if session_id:
            return int(self.db.scalar(
                "SELECT COUNT(*) FROM orders WHERE session_id=?", (session_id,), default=0) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM orders", default=0) or 0)

    def clear(self) -> int:
        return int(self.db.execute("DELETE FROM orders").rowcount or 0)

    @staticmethod
    def _to_obj(row) -> Order:
        return Order(
            order_id=row["order_id"], client_id=row["client_id"] or "",
            symbol=row["symbol"], side=row["side"], qty=int(row["qty"]),
            order_type=row["order_type"], limit_price=float(row["limit_price"] or 0),
            status=row["status"], filled_qty=int(row["filled_qty"] or 0),
            avg_fill_price=float(row["avg_fill_price"] or 0),
            created_at=text_dt(row["created_at"]), updated_at=text_dt(row["updated_at"]),
            strategy_id=row["strategy_id"] or "", session_id=row["session_id"] or "",
            reason=row["reason"] or "",
            gateway=row["gateway"] or "",
            broker_order_id=row["broker_order_id"] or "",
        )


class FillRepository(_BaseRepository):
    def save_many(self, fills: Sequence[Fill]) -> int:
        if not fills:
            return 0
        rows = [
            (f.fill_id, f.order_id, f.symbol, f.side, f.qty, f.price, dt_text(f.dt),
             f.commission, f.stamp_tax, f.transfer_fee, f.slippage_cost, f.trade_id)
            for f in fills
        ]
        self.db.executemany(
            """
            INSERT INTO fills(fill_id, order_id, symbol, side, qty, price, dt,
                commission, stamp_tax, transfer_fee, slippage_cost, trade_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fill_id) DO NOTHING
            """,
            rows,
        )
        return len(rows)

    def list_by_order(self, order_id: str) -> List[Fill]:
        rows = self.db.query(
            "SELECT * FROM fills WHERE order_id=? ORDER BY dt", (order_id,))
        return [self._to_obj(r) for r in rows]

    def list_recent(self, limit: int = 100) -> List[Fill]:
        rows = self.db.query("SELECT * FROM fills ORDER BY dt DESC LIMIT ?", (int(limit),))
        return [self._to_obj(r) for r in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM fills", default=0) or 0)

    def clear(self) -> int:
        return int(self.db.execute("DELETE FROM fills").rowcount or 0)

    @staticmethod
    def _to_obj(row) -> Fill:
        return Fill(
            fill_id=row["fill_id"], order_id=row["order_id"], symbol=row["symbol"],
            side=row["side"], qty=int(row["qty"]), price=float(row["price"]),
            dt=text_dt(row["dt"]) or datetime(1970, 1, 1),
            commission=float(row["commission"] or 0),
            stamp_tax=float(row["stamp_tax"] or 0),
            transfer_fee=float(row["transfer_fee"] or 0),
            slippage_cost=float(row["slippage_cost"] or 0),
        )


# ---------------------------------------------------------------- 权益曲线


class EquityRepository(_BaseRepository):
    def add(self, session_id: str, dt: datetime, cash: float, market_value: float,
            equity: float, day_pnl: float, drawdown: float) -> None:
        self.add_many(session_id, [(dt, cash, market_value, equity, day_pnl, drawdown)])

    def add_many(self, session_id: str, points: Sequence[Tuple[datetime, float, float, float, float, float]]) -> int:
        if not points:
            return 0
        rows = [(session_id, dt_text(dt), cash, mv, equity, pnl, dd)
                for dt, cash, mv, equity, pnl, dd in points]
        self.db.executemany(
            """
            INSERT INTO equity_snapshots(session_id, dt, cash, market_value, equity,
                day_pnl, drawdown)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(session_id, dt) DO UPDATE SET
                cash=excluded.cash, market_value=excluded.market_value,
                equity=excluded.equity, day_pnl=excluded.day_pnl,
                drawdown=excluded.drawdown
            """,
            rows,
        )
        return len(rows)

    def list_by_session(self, session_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM equity_snapshots WHERE session_id=? ORDER BY dt ASC LIMIT ?",
            (session_id, int(limit)))
        return [
            {
                "dt": text_dt(r["dt"]),
                "cash": float(r["cash"]),
                "market_value": float(r["market_value"]),
                "equity": float(r["equity"]),
                "day_pnl": float(r["day_pnl"]),
                "drawdown": float(r["drawdown"]),
            }
            for r in rows
        ]

    def latest(self, session_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM equity_snapshots WHERE session_id=? ORDER BY dt DESC LIMIT 1",
            (session_id,))
        if not rows:
            return None
        r = rows[0]
        return {"dt": text_dt(r["dt"]), "equity": float(r["equity"]),
                "cash": float(r["cash"]), "market_value": float(r["market_value"])}

    def count(self, session_id: Optional[str] = None) -> int:
        if session_id:
            return int(self.db.scalar(
                "SELECT COUNT(*) FROM equity_snapshots WHERE session_id=?",
                (session_id,), default=0) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM equity_snapshots", default=0) or 0)


    def clear(self) -> int:
        return int(self.db.execute("DELETE FROM equity_snapshots").rowcount or 0)


# ---------------------------------------------------------------- 审计


class AuditRepository(_BaseRepository):
    MAX_DETAIL = 2000

    def write(self, category: str, action: str, actor: str = "user",
              detail: str = "", severity: str = "INFO",
              ts: Optional[datetime] = None) -> int:
        detail = (detail or "")[: self.MAX_DETAIL]
        cur = self.db.execute(
            "INSERT INTO audit_log(ts, category, action, actor, detail, severity) "
            "VALUES (?,?,?,?,?,?)",
            (dt_text(ts or datetime.now()), category, action, actor, detail, severity),
        )
        return int(cur.lastrowid or 0)

    def list_recent(self, limit: int = 200, category: Optional[str] = None,
                    severity: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: List[Any] = []
        if category:
            sql += " AND category=?"
            params.append(category)
        if severity:
            sql += " AND severity=?"
            params.append(severity)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [
            {
                "id": int(r["id"]), "ts": text_dt(r["ts"]), "category": r["category"],
                "action": r["action"], "actor": r["actor"], "detail": r["detail"],
                "severity": r["severity"],
            }
            for r in self.db.query(sql, params)
        ]

    def count(self, category: Optional[str] = None) -> int:
        if category:
            return int(self.db.scalar(
                "SELECT COUNT(*) FROM audit_log WHERE category=?", (category,), default=0) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM audit_log", default=0) or 0)

    def purge_before(self, moment: datetime) -> int:
        return int(self.db.execute(
            "DELETE FROM audit_log WHERE ts < ?", (dt_text(moment),)).rowcount or 0)

    def clear(self) -> int:
        return int(self.db.execute("DELETE FROM audit_log").rowcount or 0)


# ---------------------------------------------------------------- 键值


class KvRepository(_BaseRepository):
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM kv WHERE key=?", (key,))
        return _loads(row["value"], default) if row else default

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _dumps(value), dt_text(datetime.now())),
        )

    def delete(self, key: str) -> bool:
        return bool(self.db.execute("DELETE FROM kv WHERE key=?", (key,)).rowcount)

    def all(self) -> Dict[str, Any]:
        return {r["key"]: _loads(r["value"], None)
                for r in self.db.query("SELECT key, value FROM kv ORDER BY key")}

    def clear(self) -> int:
        return int(self.db.execute("DELETE FROM kv").rowcount or 0)


# ---------------------------------------------------------------- 汇总容器


class Repositories:
    """一次性持有所有仓储，便于注入与测试。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.instruments = InstrumentRepository(db)
        self.bars = BarRepository(db)
        self.backtests = BacktestRepository(db)
        self.sessions = SessionRepository(db)
        self.orders = OrderRepository(db)
        self.fills = FillRepository(db)
        self.equity = EquityRepository(db)
        self.audit = AuditRepository(db)
        self.kv = KvRepository(db)
        self.live_events = LiveEventRepository(db)
        self.strategy_profiles = StrategyProfileRepository(db)
