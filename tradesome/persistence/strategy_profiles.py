"""策略工坊档案仓储：存 JSON 配置，不存代码。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.errors import PersistenceError
from .db import Database


class StrategyProfileRepository:
    """一条档案一行；spec_json 是完整快照，便于回滚与迁移。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _to_dict(row) -> Dict[str, Any]:
        return dict(row)

    def upsert(self, data: Dict[str, Any]) -> str:
        profile_id = str(data.get("profile_id") or "").strip()
        if not profile_id:
            raise PersistenceError("策略档案缺少 profile_id")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        created = str(data.get("created_at") or now)
        self.db.execute(
            """
            INSERT INTO strategy_profiles(profile_id, name, template, spec_json,
                                          created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
                name=excluded.name, template=excluded.template,
                spec_json=excluded.spec_json, updated_at=excluded.updated_at
            """,
            (profile_id, str(data.get("name") or ""),
             str(data.get("template") or ""),
             json.dumps(data, ensure_ascii=False), created, now),
        )
        return profile_id

    def get(self, profile_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one(
            "SELECT * FROM strategy_profiles WHERE profile_id=?", (profile_id,))
        return self._to_dict(row) if row else None

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM strategy_profiles ORDER BY updated_at DESC, name")
        return [self._to_dict(row) for row in rows]

    def delete(self, profile_id: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM strategy_profiles WHERE profile_id=?", (profile_id,))
        return bool(cur.rowcount)

    def clear(self) -> int:
        cur = self.db.execute("DELETE FROM strategy_profiles")
        return int(cur.rowcount or 0)

    def load_specs(self) -> List[Dict[str, Any]]:
        """把数据库行还原为档案 JSON，损坏行跳过并保留其余。"""
        result: List[Dict[str, Any]] = []
        for row in self.list_all():
            try:
                data = json.loads(row.get("spec_json") or "{}")
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            data["created_at"] = row.get("created_at", "")
            data["updated_at"] = row.get("updated_at", "")
            result.append(data)
        return result
