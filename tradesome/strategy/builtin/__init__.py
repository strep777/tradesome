"""内置策略集合。每个模块一个策略，import 时自动被注册表发现。"""

from __future__ import annotations

from .boll_reversion import BollReversionStrategy
from .grid import GridStrategy
from .ma_cross import MaCrossStrategy
from .momentum_rotation import MomentumRotationStrategy
from .rsi_reversal import RsiReversalStrategy
from .sentiment_boost import SentimentBoostStrategy

__all__ = [
    "MaCrossStrategy", "BollReversionStrategy", "RsiReversalStrategy",
    "GridStrategy", "MomentumRotationStrategy", "SentimentBoostStrategy",
]
