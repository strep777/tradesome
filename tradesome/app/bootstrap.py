"""应用装配：按依赖顺序创建对象，任何模块都不需要知道别人的构造细节。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from ..core.bus import EventBus
from ..core.config import load_config
from ..core.events import APP_ERROR, APP_READY
from ..core.instruments import InstrumentRegistry
from ..core.logging import get_logger, set_level, setup_logging
from ..persistence.db import Database
from ..persistence.migrations import initialize
from ..persistence.repositories import Repositories
from ..security.vault import default_vault
from ..strategy.registry import StrategyRegistry
from .container import Container
from .paths import Paths

LOGGER = get_logger("app.bootstrap")


def build_container(root: Optional[Path] = None, memory_db: bool = False,
                    with_demo: Optional[bool] = None,
                    services: bool = True) -> Container:
    paths = Paths(Path(root)) if root else Paths.default()
    paths.ensure()
    setup_logging(paths.logs, level="INFO")
    config, warnings = load_config(paths.settings)
    set_level(config.advanced.log_level)
    for warning in warnings:
        LOGGER.warning("配置：%s", warning)

    db_path = ":memory:" if memory_db else paths.db
    db = Database(db_path)
    db.connect()
    initialize(db)
    repos = Repositories(db)
    instruments = InstrumentRegistry().with_defaults()
    strategies = StrategyRegistry(user_dir=paths.strategies)
    vault = default_vault(paths.var, config.security.vault_enabled)
    bus = EventBus()
    container = Container(paths=paths, config=config, bus=bus, db=db,
                          repos=repos, instruments=instruments,
                          strategies=strategies, vault=vault)
    container.memory_db = bool(memory_db)
    if services:
        from .services import Services
        container.services = Services(container)
        repos.audit.write("app", "start", detail="TradeSome 启动")
        try:
            should_demo = (config.data.auto_generate_demo if with_demo is None
                           else with_demo)
            if should_demo:
                container.services.market.ensure_demo()
            bus.emit(APP_READY, version=_version())
        except Exception as exc:  # noqa: BLE001 - 启动失败要让界面能提示
            LOGGER.exception("启动初始化失败")
            bus.emit(APP_ERROR, message=str(exc))
            raise
    return container


def _version() -> str:
    from .. import __version__
    return __version__


def shutdown(container: Container) -> None:
    try:
        container.repos.audit.write("app", "stop", detail="TradeSome 退出")
    except Exception:  # noqa: BLE001
        pass
    container.close()
