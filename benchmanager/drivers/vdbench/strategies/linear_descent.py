"""vdbench 固有戦略: 線形降下(§4.4)。

最大負荷(§4.3)から ``step_iops`` ずつ IOPS を下げて測定する。

* 停止条件: 直前の測定点との latency 変化率が ``stop_delta_pct`` 以下
* 上限: ``max_iterations`` 回

「latency の収束を見て打ち切る」判断は vdbench の応答特性を前提にするため、
汎用 ``strategy/`` ではなく vdbench ドライバ配下に置く(§4.2)。
"""

from __future__ import annotations

from ....strategy.base import MeasurementStrategy, StrategySpec
from ....types import MeasurementResult

#: step_iops 未指定時の既定(最大負荷に対する比率)
DEFAULT_STEP_RATIO = 0.1


class VdbenchLinearDescentStrategy(MeasurementStrategy):
    """最大負荷から一定刻みで降下する戦略。"""

    name = "vdbench_linear_descent"
    requires_max_load = True

    def __init__(
        self,
        max_load_iops: float,
        step_iops: int | None = None,
        stop_delta_pct: float = 5.0,
        max_iterations: int = 10,
    ):
        self.max_load_iops = float(max_load_iops)
        self.step_iops = int(step_iops or max(1, round(self.max_load_iops * DEFAULT_STEP_RATIO)))
        self.stop_delta_pct = float(stop_delta_pct)
        self.max_iterations = int(max_iterations)

    @classmethod
    def from_spec(cls, spec: StrategySpec) -> "VdbenchLinearDescentStrategy":
        return cls(
            max_load_iops=spec.max_load_iops or 0.0,
            step_iops=spec.step_iops,
            stop_delta_pct=spec.stop_delta_pct,
            max_iterations=spec.max_iterations,
        )

    def next_target(self, history: list[MeasurementResult]) -> int | None:
        produced = self.strategy_points(history)
        if len(produced) >= self.max_iterations:
            return None

        points = self.usable_points(history)
        if not points:
            # 最大負荷点すら無い場合は最大負荷そのものから始める
            return max(1, int(round(self.max_load_iops)))

        if self._converged(points):
            return None

        # 直近の測定点(実測 IOPS が最小のもの = 一番低い負荷)から step 分下げる
        lowest = points[0]
        base = lowest.target_iops if lowest.target_iops is not None else lowest.iops
        target = int(round(base)) - self.step_iops
        if target <= 0:
            return None
        return target

    def _converged(self, points: list[MeasurementResult]) -> bool:
        """直前の 2 点の latency 変化率が停止閾値以下か。"""
        if len(points) < 2:
            return False
        # points は IOPS 昇順。降下方向なので「最後に測った 2 点」= 低い方の 2 点
        lower, upper = points[0], points[1]
        if upper.latency_avg <= 0:
            return False
        delta_pct = abs(upper.latency_avg - lower.latency_avg) / upper.latency_avg * 100.0
        return delta_pct <= self.stop_delta_pct

    def estimated_points(self) -> int:
        return self.max_iterations
