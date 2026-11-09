"""SQLite 连接封装：单连接 + 可重入锁 + 显式事务。

为什么单连接：本地单用户桌面程序，写并发极低；单连接 + WAL 足以支撑
回测线程写结果、UI 线程读列表，且避免连接池带来的跨线程陷阱。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Sequence, Union

from ..core.errors import PersistenceError

MEMORY = ":memory:"


class Database:
    """轻量数据库门面。所有 SQL 都经由此处，便于统一加日志与重试。"""

    def __init__(self, path: Union[str, Path] = MEMORY, *, timeout: float = 10.0) -> None:
        self._path = str(path)
        self._timeout = float(timeout)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._depth = 0

    # ---------------------------------------------------------- 生命周期

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_memory(self) -> bool:
        return self._path == MEMORY

    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is not None:
                return self._conn
            try:
                if not self.is_memory:
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self._path, timeout=self._timeout,
                                       check_same_thread=False, isolation_level=None)
            except sqlite3.Error as exc:
                raise PersistenceError("数据库打开失败：%s" % exc,
                                       detail=str(exc),
                                       hint="请确认程序目录可写，或关闭占用该文件的程序") from exc
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 10000")
                if not self.is_memory:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA synchronous = NORMAL")
                    conn.execute("PRAGMA temp_store = MEMORY")
            except sqlite3.Error:
                # PRAGMA 失败不致命（部分只读介质/旧版本）
                pass
            self._conn = conn
            return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
                    self._depth = 0

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---------------------------------------------------------- 执行

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self.connect().execute(sql, tuple(params))
            except sqlite3.Error as exc:
                raise PersistenceError("数据库写入失败：%s" % exc, detail="%s\nSQL: %s" % (exc, sql)) from exc

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self.connect().executemany(sql, [tuple(r) for r in rows])
            except sqlite3.Error as exc:
                raise PersistenceError("数据库批量写入失败：%s" % exc, detail="%s\nSQL: %s" % (exc, sql)) from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            try:
                cursor = self.connect().execute(sql, tuple(params))
                return cursor.fetchall()
            except sqlite3.Error as exc:
                raise PersistenceError("数据库查询失败：%s" % exc, detail="%s\nSQL: %s" % (exc, sql)) from exc

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    # ---------------------------------------------------------- 事务

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式事务；支持嵌套（内层复用外层事务）。"""
        with self._lock:
            conn = self.connect()
            if self._depth > 0:
                self._depth += 1
                try:
                    yield conn
                finally:
                    self._depth -= 1
                return
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise PersistenceError("开启事务失败：%s" % exc) from exc
            self._depth = 1
            try:
                yield conn
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                self._depth = 0
                raise
            else:
                try:
                    conn.execute("COMMIT")
                except sqlite3.Error as exc:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise PersistenceError("提交事务失败：%s" % exc) from exc
                finally:
                    self._depth = 0

    # ---------------------------------------------------------- 维护

    def table_exists(self, name: str) -> bool:
        row = self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return row is not None

    def table_names(self) -> List[str]:
        return [r[0] for r in self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    def backup(self, destination: Union[str, Path]) -> Path:
        """在线备份（使用 SQLite 官方备份 API，WAL 下安全）。"""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                dest = sqlite3.connect(str(target))
                try:
                    self.connect().backup(dest)
                finally:
                    dest.close()
            except sqlite3.Error as exc:
                raise PersistenceError("数据库备份失败：%s" % exc) from exc
        return target

    def vacuum(self) -> None:
        with self._lock:
            try:
                self.connect().execute("VACUUM")
            except sqlite3.Error as exc:
                raise PersistenceError("数据库整理失败：%s" % exc) from exc

    def size_bytes(self) -> int:
        if self.is_memory:
            return 0
        try:
            return Path(self._path).stat().st_size
        except OSError:
            return 0
