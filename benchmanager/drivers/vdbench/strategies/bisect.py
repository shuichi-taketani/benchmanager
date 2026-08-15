"""vdbench 固有戦略: 2 分探索(§4.4)。

既測定点(最大負荷点を含む)の隣接区間のうち latency 差分が最大の区間を選び、
その中点 IOPS を次の測定点にする。カーブの「膝」周辺に測定点を集中させるのが目的。

* 停止条件: 全隣接区間の latency 差分が ``stop_delta_pct`` 以下
* 上限: ``max_iterations`` 回

latency 差分は区間の 2 点のうち大きい方に対する相対値(%)として評価する
(``stop_delta_pct`` が % 指定であるため)。

最初の 1 点(下側のアンカー)は仕様書に明記が無いため、``[strategy] start_iops`` が
指定されていればそれを、無ければ最大負荷の :data:`DEFAULT_ANCHOR_RATIO` 倍を用いる。
"""

from __future__ import annotations

from ....strategy.base import MeasurementStrategy, StrategySpec
from ....types import MeasurementResult

#: 下側アンカーの既定比率(最大負荷に対する割合)
DEFAULT_ANCHOR_RATIO = 0.1

#: 中点が既測定点とこの IOPS 差未満なら測定済みとみなす
MIN_GAP_IOPS = 1


class VdbenchBisectStrategy(MeasurementStrategy):
    """latency 差分が最大の区間を 2 分割していく戦略。"""

    name = "vdbench_bisect"
    requires_max_load = True

    def __init__(
        self,
        max_load_iops: float,
        stop_delta_pct: float = 10.0,
        max_iterations: int = 10,
        anchor_iops: int | None = None,
    ):
        self.max_load_iops = float(max_load_iops)
        self.stop_delta_pct = float(stop_delta_pct)
        self.max_iterations = int(max_iterations)
        self.anchor_iops = int(
            anchor_iops or max(1, round(self.max_load_iops * DEFAULT_ANCHOR_RATIO))
        )

    @classmethod
    def from_spec(cls, spec: StrategySpec) -> "VdbenchBisectStrategy":
        return cls(
            max_load_iops=spec.max_load_iops or 0.0,
            stop_delta_pct=spec.stop_delta_pct,
            max_iterations=spec.max_iterations,
            anchor_iops=spec.start_iops,
        )

    def next_target(self, history: list[MeasurementResult]) -> int | None:
        produced = self.strategy_points(history)
        if len(produced) >= self.max_iterations:
            return None

        points = self.usable_points(history)
        if not points:
            return max(1, int(round(self.max_load_iops)))
        if len(points) < 2:
            # 区間を作るための下側アンカーをまず測る
            return self.anchor_iops

        widest = self._widest_interval(points)
        if widest is None:
            return None
        lower, upper = widest
        midpoint = int(round((lower.iops + upper.iops) / 2.0))
        if (
            midpoint - int(round(lower.iops)) < MIN_GAP_IOPS
            or int(round(upper.iops)) - midpoint < MIN_GAP_IOPS
        ):
            return None
        return midpoint

    def _widest_interval(
        self, points: list[MeasurementResult]
    ) -> tuple[MeasurementResult, MeasurementResult] | None:
        """latency 差分(%)が最大の隣接区間。全区間が閾値以下なら None。"""
        best: tuple[float, MeasurementResult, MeasurementResult] | None = None
        for lower, upper in zip(points, points[1:]):
            delta_pct = self._delta_pct(lower, upper)
            if best is None or delta_pct > best[0]:
                best = (delta_pct, lower, upper)
        if best is None or best[0] <= self.stop_delta_pct:
            return None
        return best[1], best[2]

    @staticmethod
    def _delta_pct(lower: MeasurementResult, upper: MeasurementResult) -> float:
        base = max(lower.latency_avg, upper.latency_avg)
        if base <= 0:
            return 0.0
        return abs(upper.latency_avg - lower.latency_avg) / base * 100.0

    def estimated_points(self) -> int:
        return self.max_iterations
