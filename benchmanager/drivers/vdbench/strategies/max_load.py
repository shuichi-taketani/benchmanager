"""最大負荷の計測(§4.3)。

``vdbench_linear_descent`` / ``vdbench_bisect`` の**起点**を決めるためのロジック。
汎用戦略 ``range`` はこの手順を使わない(start_iops から直接測定を開始する)。

手順:

1. ``iorate=max`` で実行し、実測 IOPS と平均 latency を得る
2. 実測 latency >= 閾値 → その実測 IOPS を最大負荷として採用
3. 実測 latency < 閾値 → 「負荷不十分」エラー(飽和に達していない)
4. ``mode = "manual"`` の場合は計測せず設定値を使用
"""

from __future__ import annotations

from dataclasses import dataclass

from ....errors import MaxLoadInsufficientError
from ....i18n import t
from ....types import MeasurementResult

MODE_MEASURE = "measure"
MODE_MANUAL = "manual"


@dataclass
class MaxLoadOutcome:
    """最大負荷の決定結果。"""

    iops: float
    latency_ms: float | None
    mode: str
    measured: bool


class MaxLoadMeasurer:
    """設定に従って最大負荷を決定する。

    実行そのものはエンジンが行い(ドライバ経由で ``target_iops=None`` の測定)、
    このクラスは「計測が必要か」「採用可否の判定」だけを担う。
    """

    def __init__(
        self,
        mode: str = MODE_MEASURE,
        latency_threshold_ms: float = 5.0,
        manual_iops: int | None = None,
    ):
        self.mode = mode
        self.latency_threshold_ms = float(latency_threshold_ms)
        self.manual_iops = manual_iops

    @classmethod
    def from_config(cls, max_load_config) -> "MaxLoadMeasurer":
        return cls(
            mode=max_load_config.mode,
            latency_threshold_ms=max_load_config.latency_threshold_ms,
            manual_iops=max_load_config.manual_iops,
        )

    @property
    def needs_measurement(self) -> bool:
        """``iorate=max`` の実測が必要か。"""
        return self.mode == MODE_MEASURE

    def resolve(self, result: MeasurementResult | None = None) -> MaxLoadOutcome:
        """最大負荷を決定する。

        :param result: ``iorate=max`` の測定結果(``mode="manual"`` のときは None)
        :raises MaxLoadInsufficientError: latency が閾値未満で飽和に達していない場合
        """
        if not self.needs_measurement:
            return MaxLoadOutcome(
                iops=float(self.manual_iops or 0),
                latency_ms=None,
                mode=MODE_MANUAL,
                measured=False,
            )
        if result is None or not result.succeeded:
            raise MaxLoadInsufficientError(
                t(
                    "engine.max_load_insufficient",
                    latency=0.0,
                    threshold=self.latency_threshold_ms,
                    iops=0.0,
                )
            )
        if result.latency_avg < self.latency_threshold_ms:
            raise MaxLoadInsufficientError(
                t(
                    "engine.max_load_insufficient",
                    latency=result.latency_avg,
                    threshold=self.latency_threshold_ms,
                    iops=result.iops,
                )
            )
        return MaxLoadOutcome(
            iops=result.iops,
            latency_ms=result.latency_avg,
            mode=MODE_MEASURE,
            measured=True,
        )
