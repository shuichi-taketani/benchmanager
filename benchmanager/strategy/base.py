"""測定戦略の抽象(§9)。

戦略は「次に測る IOPS を決める」だけの責務を持ち、ドライバの内部実装を知らない。

区分(§4.2):

* **汎用戦略**(このパッケージ): どのベンチマークツールでも使える。``range``
* **ツール固有戦略**(``drivers/<tool>/strategies/``): そのツールの応答特性への
  知識を前提とする。vdbench では ``vdbench_linear_descent`` / ``vdbench_bisect``
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..types import MeasurementResult


@dataclass
class StrategySpec:
    """戦略の生成に必要な値(設定ファイル由来 + 実行時に決まる最大負荷)。

    設定モデルそのものではなくこの中間表現を渡すことで、戦略実装が
    pydantic モデルや TOML の構造に依存しないようにする。
    """

    #: 汎用 range 戦略用
    start_iops: int | None = None
    end_iops: int | None = None
    step_iops: int | None = None
    #: ツール固有戦略用
    stop_delta_pct: float = 10.0
    max_iterations: int = 10
    #: 最大負荷(§4.3 で決定済みの値。requires_max_load な戦略でのみ使用)
    max_load_iops: float | None = None

    @classmethod
    def from_config(cls, strategy_config, max_load_iops: float | None = None) -> "StrategySpec":
        """``[strategy]`` セクションから生成する。"""
        params = strategy_config.params
        return cls(
            start_iops=strategy_config.start_iops,
            end_iops=strategy_config.end_iops,
            step_iops=(
                strategy_config.step_iops
                if strategy_config.step_iops is not None
                else params.step_iops
            ),
            stop_delta_pct=params.stop_delta_pct,
            max_iterations=params.max_iterations,
            max_load_iops=max_load_iops,
        )


class MeasurementStrategy(ABC):
    """次に測る IOPS を決める。"""

    #: 設定ファイルの ``strategy.type`` に対応する名前
    name: str = ""
    #: True の場合、エンジンは戦略ループの前に最大負荷計測(§4.3)を行う
    requires_max_load: bool = False

    @abstractmethod
    def next_target(self, history: list[MeasurementResult]) -> int | None:
        """次に測定する目標 IOPS。``None`` は停止条件成立。"""

    def estimated_points(self) -> int:
        """``benchman plan`` 用の推定測定点数(上限側の概算)。"""
        return 0

    # -- 補助 -------------------------------------------------------------
    @staticmethod
    def usable_points(history: list[MeasurementResult]) -> list[MeasurementResult]:
        """判定に使える測定点(成功したもののみ、実測 IOPS 昇順)。"""
        points = [h for h in history if h.succeeded and h.iops > 0]
        return sorted(points, key=lambda h: h.iops)

    @staticmethod
    def strategy_points(history: list[MeasurementResult]) -> list[MeasurementResult]:
        """戦略が指示した測定点(= 最大負荷計測を除く)。"""
        return [h for h in history if h.target_iops is not None]
