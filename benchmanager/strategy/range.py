"""汎用測定戦略 ``range``(§4.2, §4.4)。

``start_iops`` から ``end_iops`` まで ``step_iops`` 刻みで測定するだけの最も基本的な戦略。
最大負荷の自動探索も停止条件の判定も行わないため、ツール固有の知識を一切必要としない。
fio/ior など将来のドライバでもそのまま利用できる。
"""

from __future__ import annotations

from ..types import MeasurementResult
from .base import MeasurementStrategy, StrategySpec


class RangeStrategy(MeasurementStrategy):
    """開始・終了・ステップの 3 値のみで動作する汎用戦略。"""

    name = "range"
    requires_max_load = False

    def __init__(self, start_iops: int, end_iops: int, step_iops: int):
        if step_iops == 0:
            raise ValueError("step_iops must not be 0")
        self.start_iops = int(start_iops)
        self.end_iops = int(end_iops)
        self.step_iops = int(step_iops)
        self._targets = self._build_targets()

    @classmethod
    def from_spec(cls, spec: StrategySpec) -> "RangeStrategy":
        return cls(
            start_iops=int(spec.start_iops),
            end_iops=int(spec.end_iops),
            step_iops=int(spec.step_iops),
        )

    def _build_targets(self) -> list[int]:
        targets: list[int] = []
        value = self.start_iops
        descending = self.step_iops < 0
        while (value >= self.end_iops) if descending else (value <= self.end_iops):
            targets.append(value)
            value += self.step_iops
        return targets

    def next_target(self, history: list[MeasurementResult]) -> int | None:
        """指定範囲を順に返し、全点測定したら ``None``(停止条件なし)。"""
        index = len(self.strategy_points(history))
        if index >= len(self._targets):
            return None
        return self._targets[index]

    def estimated_points(self) -> int:
        return len(self._targets)
