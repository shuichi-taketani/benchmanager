"""測定戦略のテスト(§4.2 - §4.4、受け入れ基準 6)。"""

from __future__ import annotations

import pytest

from benchmanager import metrics as M
from benchmanager.drivers.mock.driver import MockDriver
from benchmanager.drivers.vdbench.driver import VdbenchDriver
from benchmanager.drivers.vdbench.strategies.bisect import VdbenchBisectStrategy
from benchmanager.drivers.vdbench.strategies.linear_descent import VdbenchLinearDescentStrategy
from benchmanager.drivers.vdbench.strategies.max_load import MaxLoadMeasurer
from benchmanager.errors import MaxLoadInsufficientError
from benchmanager.strategy.base import StrategySpec
from benchmanager.strategy.range import RangeStrategy
from benchmanager.types import MeasurementResult, MeasurementStatus


def point(seq: int, target: int | None, iops: float, latency: float,
          status: str = MeasurementStatus.OK) -> MeasurementResult:
    return MeasurementResult(
        seq=seq,
        target_iops=target,
        status=status,
        metrics={M.IOPS: iops, M.LATENCY_AVG: latency},
    )


# --------------------------------------------------------------------------
# 汎用戦略: range
# --------------------------------------------------------------------------


def test_range_ascending():
    strategy = RangeStrategy(10000, 50000, 10000)
    history: list[MeasurementResult] = []
    targets = []
    while (target := strategy.next_target(history)) is not None:
        targets.append(target)
        history.append(point(len(history) + 1, target, target, 1.0))
    assert targets == [10000, 20000, 30000, 40000, 50000]
    assert strategy.estimated_points() == 5


def test_range_descending():
    strategy = RangeStrategy(30000, 10000, -10000)
    history: list[MeasurementResult] = []
    targets = []
    while (target := strategy.next_target(history)) is not None:
        targets.append(target)
        history.append(point(len(history) + 1, target, target, 1.0))
    assert targets == [30000, 20000, 10000]


def test_range_has_no_stop_condition():
    """latency が収束していても指定範囲は全点測定する(汎用戦略の性質)。"""
    strategy = RangeStrategy(1000, 5000, 1000)
    history = [point(i, i * 1000, i * 1000, 1.0) for i in range(1, 5)]
    assert strategy.next_target(history) == 5000


def test_range_is_available_on_every_driver():
    assert "range" in MockDriver.available_strategies()
    assert "range" in VdbenchDriver.available_strategies()
    assert not RangeStrategy.requires_max_load


# --------------------------------------------------------------------------
# 最大負荷計測(§4.3)
# --------------------------------------------------------------------------


def test_max_load_measure_adopted():
    measurer = MaxLoadMeasurer(mode="measure", latency_threshold_ms=5.0)
    assert measurer.needs_measurement
    outcome = measurer.resolve(point(1, None, 98000, 25.0))
    assert outcome.iops == 98000
    assert outcome.measured is True


def test_max_load_insufficient_raises():
    measurer = MaxLoadMeasurer(mode="measure", latency_threshold_ms=5.0)
    with pytest.raises(MaxLoadInsufficientError) as exc:
        measurer.resolve(point(1, None, 98000, 1.2))
    assert "負荷不十分" in str(exc.value)


def test_max_load_manual_skips_measurement():
    measurer = MaxLoadMeasurer(mode="manual", manual_iops=50000)
    assert not measurer.needs_measurement
    outcome = measurer.resolve(None)
    assert outcome.iops == 50000
    assert outcome.measured is False


# --------------------------------------------------------------------------
# vdbench 固有: 線形降下(§4.4)
# --------------------------------------------------------------------------


def test_linear_descent_steps_down_from_max_load():
    strategy = VdbenchLinearDescentStrategy(
        max_load_iops=100000, step_iops=1000, stop_delta_pct=5.0, max_iterations=10
    )
    history = [point(1, None, 100000, 20.0)]
    assert strategy.next_target(history) == 99000
    history.append(point(2, 99000, 99000, 15.0))
    assert strategy.next_target(history) == 98000


def test_linear_descent_stops_on_small_latency_delta():
    strategy = VdbenchLinearDescentStrategy(
        max_load_iops=100000, step_iops=1000, stop_delta_pct=5.0, max_iterations=10
    )
    history = [
        point(1, None, 100000, 10.0),
        point(2, 99000, 99000, 9.8),  # 変化率 2% < 5%
    ]
    assert strategy.next_target(history) is None


def test_linear_descent_respects_max_iterations():
    strategy = VdbenchLinearDescentStrategy(
        max_load_iops=100000, step_iops=1000, stop_delta_pct=0.0, max_iterations=3
    )
    history = [point(1, None, 100000, 100.0)]
    for index in range(3):
        target = strategy.next_target(history)
        assert target is not None
        # latency を大きく変化させ続けて停止条件では止まらないようにする
        history.append(point(index + 2, target, target, 100.0 / (index + 2)))
    assert strategy.next_target(history) is None


def test_linear_descent_stops_before_zero():
    strategy = VdbenchLinearDescentStrategy(
        max_load_iops=1500, step_iops=1000, stop_delta_pct=0.0, max_iterations=10
    )
    history = [point(1, None, 1500, 10.0), point(2, 500, 500, 1.0)]
    assert strategy.next_target(history) is None


# --------------------------------------------------------------------------
# vdbench 固有: 2 分探索(§4.4)
# --------------------------------------------------------------------------


def test_bisect_measures_anchor_first():
    strategy = VdbenchBisectStrategy(max_load_iops=100000, anchor_iops=10000)
    history = [point(1, None, 100000, 20.0)]
    assert strategy.next_target(history) == 10000


def test_bisect_picks_widest_latency_interval():
    strategy = VdbenchBisectStrategy(
        max_load_iops=100000, stop_delta_pct=10.0, max_iterations=10, anchor_iops=10000
    )
    history = [
        point(1, None, 100000, 20.0),
        point(2, 10000, 10000, 1.0),
        point(3, 55000, 55000, 1.5),  # 10000-55000 は差が小さい、55000-100000 が最大
    ]
    assert strategy.next_target(history) == 77500


def test_bisect_stops_when_all_intervals_are_flat():
    strategy = VdbenchBisectStrategy(
        max_load_iops=100000, stop_delta_pct=10.0, max_iterations=10, anchor_iops=10000
    )
    history = [
        point(1, None, 100000, 10.0),
        point(2, 10000, 10000, 9.5),
    ]
    assert strategy.next_target(history) is None


def test_bisect_respects_max_iterations():
    strategy = VdbenchBisectStrategy(
        max_load_iops=100000, stop_delta_pct=0.0, max_iterations=2, anchor_iops=10000
    )
    history = [point(1, None, 100000, 20.0)]
    history.append(point(2, 10000, 10000, 1.0))
    history.append(point(3, 55000, 55000, 5.0))
    assert strategy.next_target(history) is None


def test_bisect_ignores_failed_points():
    strategy = VdbenchBisectStrategy(max_load_iops=100000, anchor_iops=10000)
    history = [
        point(1, None, 100000, 20.0),
        point(2, 10000, 0, 0, status=MeasurementStatus.FAILED),
    ]
    # 失敗点は判定に使わないので、まだアンカーが必要
    assert strategy.next_target(history) == 10000


# --------------------------------------------------------------------------
# 分離(受け入れ基準 6)
# --------------------------------------------------------------------------


def test_vdbench_strategies_are_not_in_generic_registry():
    from benchmanager.strategy.registry import GENERIC_STRATEGIES

    assert set(GENERIC_STRATEGIES) == {"range"}
    assert "vdbench_bisect" not in GENERIC_STRATEGIES


def test_driver_creates_strategy_from_spec():
    spec = StrategySpec(start_iops=1000, end_iops=3000, step_iops=1000)
    strategy = MockDriver.create_strategy("range", spec)
    assert isinstance(strategy, RangeStrategy)

    spec = StrategySpec(stop_delta_pct=10, max_iterations=5, max_load_iops=50000)
    strategy = VdbenchDriver.create_strategy("vdbench_bisect", spec)
    assert isinstance(strategy, VdbenchBisectStrategy)
    assert VdbenchDriver.requires_max_load("vdbench_bisect") is True
    assert VdbenchDriver.requires_max_load("range") is False
