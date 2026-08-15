"""汎用戦略のレジストリ。

ここには**全ドライバ共通で使える戦略のみ**を登録する。
ツール固有の戦略は各ドライバのサブパッケージ側に置き、
``BenchDriver.create_strategy()`` で解決する(§4.2, §9)。
"""

from __future__ import annotations

from .base import MeasurementStrategy, StrategySpec
from .range import RangeStrategy

#: 汎用戦略名 -> 生成関数
GENERIC_STRATEGIES: dict[str, type[MeasurementStrategy]] = {
    RangeStrategy.name: RangeStrategy,
}

#: 全ドライバが必ず対応する戦略名
GENERIC_STRATEGY_NAMES: tuple[str, ...] = tuple(GENERIC_STRATEGIES)


def is_generic(name: str) -> bool:
    return name in GENERIC_STRATEGIES


def create_generic_strategy(name: str, spec: StrategySpec) -> MeasurementStrategy:
    """汎用戦略を生成する。"""
    strategy_cls = GENERIC_STRATEGIES[name]
    return strategy_cls.from_spec(spec)  # type: ignore[attr-defined]
