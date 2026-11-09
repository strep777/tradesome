"""策略子系统：参数声明、上下文、指标、注册表与沙箱。"""

from __future__ import annotations

from .base import ParamSpec, Strategy, StrategyMeta, coerce_params, default_params
from .context import StrategyContext, TradingGateway
from .registry import StrategyRegistry, default_registry

__all__ = [
    "ParamSpec", "Strategy", "StrategyMeta", "coerce_params", "default_params",
    "StrategyContext", "TradingGateway", "StrategyRegistry", "default_registry",
]
