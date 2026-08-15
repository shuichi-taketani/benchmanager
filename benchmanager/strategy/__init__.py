"""汎用測定戦略(全ドライバ共通)。"""

from .base import MeasurementStrategy, StrategySpec
from .range import RangeStrategy
from .registry import GENERIC_STRATEGY_NAMES, create_generic_strategy, is_generic

__all__ = [
    "GENERIC_STRATEGY_NAMES",
    "MeasurementStrategy",
    "RangeStrategy",
    "StrategySpec",
    "create_generic_strategy",
    "is_generic",
]
