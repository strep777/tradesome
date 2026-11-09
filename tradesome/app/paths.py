"""应用目录布局：所有可变数据都放在程序目录下的 var/，方便绿色部署与备份。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path

    # ---- 目录 ----
    @property
    def var(self) -> Path:
        return self.root / "var"

    @property
    def logs(self) -> Path:
        return self.var / "logs"

    @property
    def backups(self) -> Path:
        return self.var / "backups"

    @property
    def strategies(self) -> Path:
        return self.var / "strategies"

    @property
    def exports(self) -> Path:
        return self.var / "exports"

    @property
    def demo(self) -> Path:
        return self.var / "demo"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def docs(self) -> Path:
        return self.root / "doc"

    # ---- 文件 ----
    @property
    def db(self) -> Path:
        return self.var / "tradesome.db"

    @property
    def settings(self) -> Path:
        return self.var / "settings.json"

    @property
    def secrets(self) -> Path:
        return self.var / "secrets.dat"

    def ensure(self) -> "Paths":
        for folder in (self.var, self.logs, self.backups, self.strategies,
                       self.exports, self.demo, self.data_dir):
            folder.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls) -> "Paths":
        return cls(Path(__file__).resolve().parents[2])
