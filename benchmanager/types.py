"""層をまたぐ共通データ型。

ドライバ・戦略・エンジン・ストア・レポートはこの型だけを介してやり取りする
(通信層や GUI に依存する型をここに入れないこと)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import metrics as M


@dataclass(frozen=True)
class Timing:
    """測定の時間設定(§4.1)。"""

    duration_sec: int = 600
    warmup_sec: int = 60
    interval_sec: int = 1

    @property
    def total_sec(self) -> int:
        return self.duration_sec + self.warmup_sec


@dataclass(frozen=True)
class Metric:
    """正規化された 1 メトリクス(縦持ち)。"""

    name: str
    value: float
    unit: str = ""
    source: str = ""

    @staticmethod
    def make(name: str, value: float, source: str) -> "Metric":
        return Metric(name=name, value=float(value), unit=M.unit_for(name), source=source)


@dataclass(frozen=True)
class TimeseriesPoint:
    """時系列メトリクスの 1 点。"""

    name: str
    ts: float  # 測定開始からの経過秒
    value: float
    source: str = ""


@dataclass
class RawResult:
    """1 測定点の生出力。

    生データはディレクトリごと保存され、`parse()` はこのディレクトリのみを入力とする
    (= 実行なしで何度でも再パースできる。§5.2)。
    """

    directory: Path
    tool: str
    exit_code: int = 0
    command: str = ""
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class MeasurementStatus:
    """測定点 / テスト条件のステータス。"""

    OK = "ok"
    WARN = "warn"  # 実行はできたが目標 IOPS 未達など
    FAILED = "failed"
    RUNNING = "running"


@dataclass
class MeasurementResult:
    """1 測定点の結果(戦略はこの履歴を見て次の目標 IOPS を決める)。"""

    seq: int
    #: 目標 IOPS。``None`` は最大負荷計測(vdbench の ``iorate=max``)
    target_iops: int | None
    status: str = MeasurementStatus.OK
    metrics: dict[str, float] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    raw_dir: Path | None = None
    measurement_id: int | None = None
    error: str = ""

    @property
    def iops(self) -> float:
        return float(self.metrics.get(M.IOPS, 0.0))

    @property
    def latency_avg(self) -> float:
        return float(self.metrics.get(M.LATENCY_AVG, 0.0))

    @property
    def succeeded(self) -> bool:
        return self.status in (MeasurementStatus.OK, MeasurementStatus.WARN)


@dataclass
class TestRunResult:
    """1 テスト条件(= マトリクスの 1 点)の結果。"""

    index: int
    conditions: dict[str, Any]
    status: str = MeasurementStatus.OK
    measurements: list[MeasurementResult] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    raw_dir: Path | None = None
    error: str = ""


@dataclass
class CommandResult:
    """リモートコマンドの実行結果。"""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()
