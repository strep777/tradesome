"""容器：把基础设施与服务的组装集中在一处，UI 只依赖容器。"""

from __future__ import annotations

from typing import Optional

from ..backtest.metrics import format_metric
from ..core.bus import EventBus
from ..core.clock import RealClock
from ..core.config import AppConfig, save_config
from ..core.logging import get_logger
from ..core.instruments import InstrumentRegistry, default_registry
from ..persistence.db import Database
from ..persistence.migrations import initialize
from ..persistence.repositories import Repositories
from ..security.vault import SecretVault
from ..strategy.registry import StrategyRegistry
from .paths import Paths

LOGGER = get_logger("app.container")


class Container:
    def __init__(self, paths: Paths, config: AppConfig, bus: EventBus,
                 db: Database, repos: Repositories,
                 instruments: InstrumentRegistry, strategies: StrategyRegistry,
                 vault: SecretVault) -> None:
        self.paths = paths
        self.config = config
        self.bus = bus
        self.clock = RealClock()
        self.db = db
        self.repos = repos
        self.instruments = instruments
        self.strategies = strategies
        self.vault = vault
        # services 在 bootstrap 末尾注入，避免循环导入
        self.services = None
        self.closed = False

    # ---- 配置 ----

    def save_config(self) -> None:
        save_config(self.config, self.paths.settings)

    def format_metric(self, key: str, value) -> str:
        return format_metric(key, value)

    # ---- 生命周期 ----

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.services is not None:
                self.services.shutdown()
        except Exception:  # noqa: BLE001 - 退出阶段不再抛错
            LOGGER.exception("关闭服务失败")
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            LOGGER.exception("关闭数据库失败")
