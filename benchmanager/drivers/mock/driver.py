"""テスト・デモ用の疑似ドライバ(§10)。

合成 IOPS-Latency カーブ(飽和モデル)を返すため、実ストレージも実ベンチマークツールも
不要でエンジン全体を検証できる。``benchman run examples/mock`` でデモにも使える。

カーブ::

    latency(iops) = base_latency_ms + k / (max_iops - iops) + noise

``target_iops`` が ``max_iops`` を超える場合は飽和し、実測 IOPS は ``max_iops`` 近傍に張り付く。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from ... import metrics as M
from ...errors import MeasurementError
from ...i18n import t
from ...remote.base import RemoteExecutor
from ...strategy.base import MeasurementStrategy, StrategySpec
from ...strategy.registry import GENERIC_STRATEGIES, is_generic
from ...types import Metric, RawResult, TimeseriesPoint, Timing
from ..base import BenchDriver
from ..vdbench.strategies import (
    VDBENCH_STRATEGIES,
    VDBENCH_STRATEGY_NAMES,
    create_vdbench_strategy,
)

RESULT_FILENAME = "result.json"
SOURCE = "mock"

#: 飽和時に実測 IOPS が到達する上限比率
SATURATION_RATIO = 0.98


def _parse_size(value: Any, default: int = 4096) -> int:
    """``"4k"`` のようなサイズ表記をバイト数に変換する。"""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().rstrip("b")
    units = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    if text and text[-1] in units:
        try:
            return int(float(text[:-1]) * units[text[-1]])
        except ValueError:
            return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _noise(seed: str, amplitude: float) -> float:
    """決定論的な擬似ノイズ(-amplitude..+amplitude)。テストの再現性を保つ。"""
    if amplitude <= 0:
        return 0.0
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF  # 0..1
    return (unit * 2 - 1) * amplitude


class MockDriver(BenchDriver):
    """合成カーブを返す疑似ドライバ。"""

    tool_name = "mock"
    supports_max_load = True

    # -- 測定戦略 ---------------------------------------------------------
    # モックは vdbench の代役(§10)なので、汎用戦略に加えて vdbench 固有戦略も
    # 実機なしで検証できるよう受け付ける。実装そのものは vdbench ドライバ配下に
    # 置いたままなので、汎用/ツール固有の分離は崩していない。
    @classmethod
    def tool_strategies(cls) -> list[str]:
        return list(VDBENCH_STRATEGY_NAMES)

    @classmethod
    def create_strategy(cls, name: str, spec: StrategySpec) -> MeasurementStrategy:
        if name in VDBENCH_STRATEGIES:
            return create_vdbench_strategy(name, spec)
        return super().create_strategy(name, spec)

    @classmethod
    def strategy_class(cls, name: str) -> type[MeasurementStrategy]:
        if name in VDBENCH_STRATEGIES:
            return VDBENCH_STRATEGIES[name]
        if is_generic(name):
            return GENERIC_STRATEGIES[name]
        return super().strategy_class(name)

    # -- 実行先 -----------------------------------------------------------
    @classmethod
    def create_executor(cls, server: Any) -> RemoteExecutor:
        """疑似ドライバは実接続しない(デモ・テストをネットワークなしで完結させる)。"""
        from ...remote.mock import MockRemoteExecutor

        return MockRemoteExecutor(name=getattr(server, "name", "mock"))

    @classmethod
    def create_management_executor(cls, management: Any, name: str = "storage") -> RemoteExecutor:
        from ...remote.mock import MockRemoteExecutor

        return MockRemoteExecutor(name=name)

    # -- 設定生成 ---------------------------------------------------------
    def generate_config(self, conditions: dict[str, Any], target_iops: int | None) -> str:
        """実行条件を JSON として書き出す(vdbench の parmfile 相当)。"""
        payload = dict(conditions)
        payload["target_iops"] = "max" if target_iops is None else target_iops
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)

    # -- 実行 -------------------------------------------------------------
    async def run(
        self,
        remote: RemoteExecutor,
        config: str,
        timing: Timing,
        output_dir: Path,
    ) -> RawResult:
        conditions = json.loads(config)
        target = conditions.get("target_iops")
        target_iops = None if target == "max" else int(target)

        # 実際には負荷をかけないが、リモート実行の経路は通す(モック RemoteExecutor 検証)
        command = f"mock-bench --target {target if target is not None else 'max'}"
        await remote.put_text(config, f"/tmp/benchman-mock/{output_dir.name}.json")
        result = await remote.run(command)

        fail_at = int(conditions.get("fail_at_iops") or 0)
        if fail_at and target_iops == fail_at:
            raise MeasurementError(t("driver.exit_nonzero", tool=self.tool_name, rc=1))
        if not result.ok:
            raise MeasurementError(
                t("driver.exit_nonzero", tool=self.tool_name, rc=result.exit_code)
            )

        sample = self.simulate(conditions, target_iops)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / RESULT_FILENAME).write_text(
            json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "config.json").write_text(config, encoding="utf-8")
        return RawResult(
            directory=output_dir,
            tool=self.tool_name,
            exit_code=0,
            command=command,
            stdout=result.stdout,
        )

    # -- 合成カーブ -------------------------------------------------------
    def simulate(self, conditions: dict[str, Any], target_iops: int | None) -> dict[str, Any]:
        """条件から合成された測定結果を作る(純関数。テストから直接呼べる)。"""
        max_iops = float(conditions.get("max_iops") or 100000)
        base = float(conditions.get("base_latency_ms") or 0.4)
        k = float(conditions.get("k") or 50000.0)
        noise_pct = float(conditions.get("noise_pct") or 0.0)
        read_pct = float(conditions.get("read_pct", 100))
        block_bytes = _parse_size(conditions.get("block_size", "4k"))

        # ブロックサイズと read 比率で到達可能な最大 IOPS を変える
        size_factor = math.sqrt(4096.0 / max(block_bytes, 1))
        read_factor = 0.6 + 0.4 * (read_pct / 100.0)
        effective_max = max(max_iops * size_factor * read_factor, 1.0)

        ceiling = effective_max * SATURATION_RATIO
        if target_iops is None:
            achieved = ceiling
        else:
            achieved = min(float(target_iops), ceiling)

        seed = f"{sorted(conditions.items())}|{target_iops}"
        achieved *= 1.0 + _noise(seed + "|iops", noise_pct / 100.0)
        achieved = max(achieved, 1.0)

        headroom = max(effective_max - achieved, effective_max * 0.005)
        latency = base + k / headroom
        latency *= 1.0 + _noise(seed + "|lat", noise_pct / 100.0)

        throughput = achieved * block_bytes / (1024.0 * 1024.0)
        return {
            "target_iops": target_iops,
            "effective_max_iops": effective_max,
            "iops": achieved,
            "latency_avg_ms": latency,
            "latency_max_ms": latency * 8.0,
            "latency_stddev_ms": latency * 0.35,
            "latency_p95_ms": latency * 2.0,
            "latency_p99_ms": latency * 4.0,
            "read_pct": read_pct,
            "block_bytes": block_bytes,
            "throughput_mbps": throughput,
            "queue_depth": achieved * latency / 1000.0,
        }

    # -- パース -----------------------------------------------------------
    def parse(self, raw: RawResult) -> list[Metric]:
        path = Path(raw.directory) / RESULT_FILENAME
        if not path.is_file():
            raise MeasurementError(t("driver.flatfile_missing", path=path))
        data = json.loads(path.read_text(encoding="utf-8"))
        read_ratio = float(data["read_pct"]) / 100.0
        iops = float(data["iops"])
        values = {
            M.IOPS: iops,
            M.READ_IOPS: iops * read_ratio,
            M.WRITE_IOPS: iops * (1.0 - read_ratio),
            M.LATENCY_AVG: float(data["latency_avg_ms"]),
            M.LATENCY_MAX: float(data["latency_max_ms"]),
            M.LATENCY_STDDEV: float(data["latency_stddev_ms"]),
            M.LATENCY_P95: float(data["latency_p95_ms"]),
            M.LATENCY_P99: float(data["latency_p99_ms"]),
            M.THROUGHPUT_MBPS: float(data["throughput_mbps"]),
            M.QUEUE_DEPTH: float(data["queue_depth"]),
            M.READ_PCT: float(data["read_pct"]),
            M.XFERSIZE_BYTES: float(data["block_bytes"]),
        }
        return [Metric.make(name, value, SOURCE) for name, value in values.items()]

    def parse_timeseries(self, raw: RawResult) -> list[TimeseriesPoint]:
        """合成の時系列(定常値 + 決定論的ノイズ)を 10 点だけ作る。"""
        path = Path(raw.directory) / RESULT_FILENAME
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        points: list[TimeseriesPoint] = []
        for index in range(10):
            factor = 1.0 + _noise(f"{path}|{index}", 0.02)
            points.append(
                TimeseriesPoint(M.IOPS, float(index), float(data["iops"]) * factor, SOURCE)
            )
            points.append(
                TimeseriesPoint(
                    M.LATENCY_AVG, float(index), float(data["latency_avg_ms"]) * factor, SOURCE
                )
            )
        return points
