"""应用服务层：界面只依赖这层，服务层再调用领域模块。

新增一个页面时，通常只需在这里加一个服务，不必改动已有服务。
"""

from __future__ import annotations

from .backtest_service import BacktestService
from .live_service import LiveService
from .live_trade_service import LiveTradeService
from .market_service import MarketService
from .report_service import ReportService
from .strategy_service import StrategyService

__all__ = ["Services", "MarketService", "StrategyService", "BacktestService",
           "LiveService", "LiveTradeService", "ReportService"]


class Services:
    """服务容器：延迟创建，按需取用。"""

    def __init__(self, container) -> None:
        self._container = container
        self.market = MarketService(container)
        self.strategy = StrategyService(container)
        self.backtest = BacktestService(container)
        self.live = LiveService(container)
        self.live_trade = LiveTradeService(container)
        self.report = ReportService(container)

    def shutdown(self) -> None:
        for name in ("live", "live_trade", "backtest"):
            service = getattr(self, name, None)
            if service is None:
                continue
            try:
                service.shutdown()
            except Exception:  # noqa: BLE001 - 退出阶段尽力而为
                pass
