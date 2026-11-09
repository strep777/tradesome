"""实盘事件仓储：连接、回报、拒单等流水，用于事后排查。

单独一个文件是因为这些事件只在实盘出现，和 A 股研究数据的仓储解耦；
`repositories.py` 只负责把它们汇总到 Repositories。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .db import Database


def _dt_text(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


class LiveEventRepository:
    """实盘连接、回报、拒单等事件流水，用于事后排查。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, session_id: str, kind: str, detail: str = "",
            ts: Optional[datetime] = None) -> None:
        self.db.execute(
            "INSERT INTO live_events(ts, session_id, kind, detail) VALUES (?,?,?,?)",
            (_dt_text(ts or datetime.now()), session_id or "", kind or "", detail or ""),
        )

    def list_by_session(self, session_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM live_events WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, int(limit)))
        return [dict(row) for row in rows]

    def list_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM live_events ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def clear(self, session_id: Optional[str] = None) -> None:
        if session_id:
            self.db.execute("DELETE FROM live_events WHERE session_id=?", (session_id,))
        else:
            self.db.execute("DELETE FROM live_events")
