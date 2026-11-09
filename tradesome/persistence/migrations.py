"""版本化数据库迁移。

规则：
1. 每个版本的 SQL 不可再修改，只能追加新版本；
2. 迁移在单个事务内完成，失败则整体回滚，数据库保持旧版本；
3. schema_version 记录"已应用到第几版"，启动时幂等执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from ..core.errors import PersistenceError
from .db import Database

SCHEMA_VERSION = 3

# (version, description, [sql, ...])
MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (
        1,
        "初始结构：标的、行情、回测、会话、订单、成交、权益、审计、键值",
        [
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS instruments (
                symbol          TEXT PRIMARY KEY,
                name            TEXT NOT NULL DEFAULT '',
                kind            TEXT NOT NULL DEFAULT 'STOCK',
                lot_size        INTEGER NOT NULL DEFAULT 100,
                price_limit_pct REAL NOT NULL DEFAULT 0.1,
                t_plus          INTEGER NOT NULL DEFAULT 1,
                tick_size       REAL NOT NULL DEFAULT 0.01,
                board           TEXT NOT NULL DEFAULT 'MAIN',
                fee_json        TEXT NOT NULL DEFAULT '{}',
                updated_at      TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS bars (
                symbol    TEXT NOT NULL,
                dt        TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '1d',
                open      REAL NOT NULL,
                high      REAL NOT NULL,
                low       REAL NOT NULL,
                close     REAL NOT NULL,
                volume    REAL NOT NULL DEFAULT 0,
                amount    REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, dt, timeframe)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_bars_symbol_dt ON bars(symbol, dt)",
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id         TEXT PRIMARY KEY,
                created_at     TEXT NOT NULL,
                strategy_key   TEXT NOT NULL DEFAULT '',
                strategy_name  TEXT NOT NULL DEFAULT '',
                start_date     TEXT NOT NULL DEFAULT '',
                end_date       TEXT NOT NULL DEFAULT '',
                initial_cash   REAL NOT NULL DEFAULT 0,
                metrics_json   TEXT NOT NULL DEFAULT '{}',
                config_json    TEXT NOT NULL DEFAULT '{}',
                equity_json    TEXT NOT NULL DEFAULT '[]',
                drawdown_json  TEXT NOT NULL DEFAULT '[]',
                monthly_json   TEXT NOT NULL DEFAULT '{}',
                benchmark_json TEXT NOT NULL DEFAULT '[]',
                duration_sec   REAL NOT NULL DEFAULT 0,
                cancelled      INTEGER NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_bt_created ON backtest_runs(created_at)",
            """
            CREATE TABLE IF NOT EXISTS backtest_trades (
                run_id       TEXT NOT NULL,
                seq          INTEGER NOT NULL,
                symbol       TEXT NOT NULL,
                open_dt      TEXT,
                close_dt     TEXT,
                qty          INTEGER NOT NULL DEFAULT 0,
                entry_price  REAL NOT NULL DEFAULT 0,
                exit_price   REAL NOT NULL DEFAULT 0,
                pnl          REAL NOT NULL DEFAULT 0,
                pnl_pct      REAL NOT NULL DEFAULT 0,
                fees         REAL NOT NULL DEFAULT 0,
                holding_days INTEGER NOT NULL DEFAULT 0,
                exit_reason  TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, seq),
                FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id          TEXT PRIMARY KEY,
                strategy_key        TEXT NOT NULL DEFAULT '',
                strategy_params_json TEXT NOT NULL DEFAULT '{}',
                symbols_json        TEXT NOT NULL DEFAULT '[]',
                started_at          TEXT NOT NULL,
                stopped_at          TEXT,
                status              TEXT NOT NULL DEFAULT 'RUNNING',
                initial_cash        REAL NOT NULL DEFAULT 0,
                final_equity        REAL NOT NULL DEFAULT 0,
                speed               REAL NOT NULL DEFAULT 1.0,
                note                TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at)",
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id       TEXT PRIMARY KEY,
                client_id      TEXT UNIQUE,
                session_id     TEXT NOT NULL DEFAULT '',
                strategy_id    TEXT NOT NULL DEFAULT '',
                symbol         TEXT NOT NULL,
                side           TEXT NOT NULL,
                qty            INTEGER NOT NULL,
                order_type     TEXT NOT NULL DEFAULT 'MARKET',
                limit_price    REAL NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'PENDING',
                filled_qty     INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL NOT NULL DEFAULT 0,
                created_at     TEXT,
                updated_at     TEXT,
                reason         TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_id, created_at)",
            """
            CREATE TABLE IF NOT EXISTS fills (
                fill_id      TEXT PRIMARY KEY,
                order_id     TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                side         TEXT NOT NULL,
                qty          INTEGER NOT NULL,
                price        REAL NOT NULL,
                dt           TEXT NOT NULL,
                commission   REAL NOT NULL DEFAULT 0,
                stamp_tax    REAL NOT NULL DEFAULT 0,
                transfer_fee REAL NOT NULL DEFAULT 0,
                slippage_cost REAL NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id)",
            """
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                session_id TEXT NOT NULL,
                dt         TEXT NOT NULL,
                cash       REAL NOT NULL DEFAULT 0,
                market_value REAL NOT NULL DEFAULT 0,
                equity     REAL NOT NULL DEFAULT 0,
                day_pnl    REAL NOT NULL DEFAULT 0,
                drawdown   REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, dt)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                category TEXT NOT NULL,
                action   TEXT NOT NULL,
                actor    TEXT NOT NULL DEFAULT 'user',
                detail   TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'INFO'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
            """
            CREATE TABLE IF NOT EXISTS kv (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ],
    ),
    (
        2,
        "实盘：订单网关字段、成交 trade_id、会话模式、实盘事件",
        [
            "ALTER TABLE orders ADD COLUMN gateway TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN broker_order_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE fills ADD COLUMN trade_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'paper'",
            "ALTER TABLE sessions ADD COLUMN gateway TEXT NOT NULL DEFAULT ''",
            """
            CREATE TABLE IF NOT EXISTS live_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                kind       TEXT NOT NULL DEFAULT '',
                detail     TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_live_events_session ON live_events(session_id, ts)",
        ],
    ),
    (
        3,
        "策略工坊：用参数与风控叠加层保存的自定义策略档案",
        [
            """
            CREATE TABLE IF NOT EXISTS strategy_profiles (
                profile_id TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                template   TEXT NOT NULL,
                spec_json  TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_strategy_profiles_updated "
            "ON strategy_profiles(updated_at)",
        ],
    ),
]


def current_version(db: Database) -> int:
    """返回数据库已应用的版本；空库返回 0。"""
    if not db.table_exists("schema_version"):
        return 0
    try:
        return int(db.scalar("SELECT MAX(version) FROM schema_version", default=0) or 0)
    except (PersistenceError, TypeError, ValueError):
        return 0


def pending_migrations(db: Database) -> List[Tuple[int, str]]:
    applied = current_version(db)
    return [(v, d) for v, d, _sql in MIGRATIONS if v > applied]


def migrate(db: Database) -> int:
    """执行所有未应用的迁移，返回最终版本号。"""
    applied = current_version(db)
    if applied > SCHEMA_VERSION:
        raise PersistenceError(
            "数据库版本（%d）高于当前程序支持的版本（%d）" % (applied, SCHEMA_VERSION),
            hint="请升级程序，或恢复与新版本匹配的备份",
        )
    for version, description, statements in MIGRATIONS:
        if version <= applied:
            continue
        try:
            with db.transaction() as conn:
                for sql in statements:
                    conn.execute(sql)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now().isoformat(sep=" ", timespec="seconds")),
                )
        except Exception as exc:
            raise PersistenceError(
                "数据库升级到 v%d 失败：%s" % (version, exc),
                detail="%s\n%s" % (description, exc),
                hint="请恢复 var/backups 中的最近备份后重试",
            ) from exc
        applied = version
    return applied


def initialize(db: Database) -> int:
    """打开连接并迁移到最新版本。"""
    db.connect()
    return migrate(db)
