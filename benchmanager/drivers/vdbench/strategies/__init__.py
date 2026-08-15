"""vdbench 固有の測定戦略(§4.2, §9)。

汎用戦略(``benchmanager/strategy/``)と実装上分離するため、これらは
vdbench ドライバのサブパッケージに置く。他ツール用の適応的戦略を追加する場合も
それぞれのドライバ配下に置く方針とする。
"""

from ....strategy.base import MeasurementStrategy, StrategySpec
from .bisect import VdbenchBisectStrategy
from .linear_descent import VdbenchLinearDescentStrategy
from .max_load import MaxLoadMeasurer, MaxLoadOutcome

#: 戦略名 -> クラス
VDBENCH_STRATEGIES: dict[str, type[MeasurementStrategy]] = {
    VdbenchLinearDescentStrategy.name: VdbenchLinearDescentStrategy,
    VdbenchBisectStrategy.name: VdbenchBisectStrategy,
}

VDBENCH_STRATEGY_NAMES: tuple[str, ...] = tuple(VDBENCH_STRATEGIES)


def create_vdbench_strategy(name: str, spec: StrategySpec) -> MeasurementStrategy:
    strategy_cls = VDBENCH_STRATEGIES[name]
    return strategy_cls.from_spec(spec)  # type: ignore[attr-defined]


__all__ = [
    "MaxLoadMeasurer",
    "MaxLoadOutcome",
    "VDBENCH_STRATEGIES",
    "VDBENCH_STRATEGY_NAMES",
    "VdbenchBisectStrategy",
    "VdbenchLinearDescentStrategy",
    "create_vdbench_strategy",
]
