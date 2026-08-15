"""実行エンジン(§9)。

責務は「マトリクス展開 → env_params 適用(order 順)→ 最大負荷計測 → strategy ループ
→ 生データ保存 → インポート」のオーケストレーションのみ。

**設計制約**: このモジュールは通信層(WebSocket など)・GUI を一切知らない。
Phase 3 で Agent 化する際、エンジンをそのまま通信層の下に置けるようにするため、
CLI からは「ローカルのジョブ定義でエンジンを直接呼ぶ」形でしか触らない。
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import const
from .config.loader import Config
from .drivers.base import BenchDriver
from .drivers.registry import get_driver_class
from .drivers.vdbench.strategies.max_load import MaxLoadMeasurer, MaxLoadOutcome
from .envparams.applier import TARGET_SERVER, TARGET_STORAGE, EnvParamApplier
from .errors import (
    BenchmanError,
    MaxLoadInsufficientError,
    MeasurementError,
    RemoteError,
)
from .i18n import t
from .remote.base import RemoteExecutor
from .store.importer import ImportStats, import_results
from .store.results import ResultsWriter, suite_dirname
from .store.store import Store
from .strategy.base import StrategySpec
from .types import (
    MeasurementResult,
    MeasurementStatus,
    TestRunResult,
    Timing,
)

logger = logging.getLogger(__name__)

#: 実測 IOPS がこの比率を下回ったら「目標未達」警告(§4.5)
IOPS_SHORTFALL_RATIO = 0.95

#: 測定点の失敗時のリトライ回数(§4.5: 1 回リトライ)
MEASUREMENT_RETRIES = 1


# --------------------------------------------------------------------------
# マトリクス展開
# --------------------------------------------------------------------------


@dataclass
class MatrixPoint:
    """テスト条件マトリクスの 1 点。"""

    index: int
    env_values: dict[str, Any] = field(default_factory=dict)
    tool_values: dict[str, Any] = field(default_factory=dict)
    #: ツールパラメータと環境パラメータを同列にした条件(conditions_json に入る)
    conditions: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.conditions.items())

    def sweep_label(self) -> str:
        """掃引軸だけのラベル(レポートのサブプロット見出し用)。"""
        values = {**self.env_values, **self.tool_values}
        return ", ".join(f"{k}={v}" for k, v in values.items()) or "-"


def expand_matrix(config: Config) -> list[MatrixPoint]:
    """マトリクスを展開する。

    * 環境パラメータが外側、ツールパラメータが常に最内(§6.1 の ``order`` 規約)
    * 環境パラメータ同士は ``order`` 昇順(小さいほど外側 = 切替コスト大)
    * この順序で直積を取ることが、そのまま環境パラメータの切替回数最小化になる
    """
    matrix = config.test.matrix
    env_axes: list[tuple[str, list[Any]]] = []
    for name, definition in config.env_params_in_order():
        values = matrix.get(name, definition.values)
        env_axes.append((name, list(values)))

    env_names = {name for name, _ in env_axes}
    tool_axes = [(name, list(values)) for name, values in matrix.items() if name not in env_names]

    axes = env_axes + tool_axes
    base_conditions = dict(config.tool_params)

    points: list[MatrixPoint] = []
    combos = itertools.product(*[values for _, values in axes]) if axes else [()]
    for index, combo in enumerate(combos, start=1):
        selected = dict(zip([name for name, _ in axes], combo))
        env_values = {name: selected[name] for name, _ in env_axes}
        tool_values = {name: selected[name] for name, _ in tool_axes}
        conditions = {**base_conditions, **tool_values, **env_values}
        points.append(
            MatrixPoint(
                index=index,
                env_values=env_values,
                tool_values=tool_values,
                conditions=conditions,
            )
        )
    return points


def count_env_switches(points: list[MatrixPoint]) -> tuple[int, int]:
    """環境パラメータの切替回数(最適化後, 素朴に毎回適用した場合)を返す。"""
    optimized = 0
    naive = 0
    current: dict[str, Any] = {}
    for point in points:
        for name, value in point.env_values.items():
            naive += 1
            if current.get(name) != value:
                optimized += 1
                current[name] = value
    return optimized, naive


# --------------------------------------------------------------------------
# 実行結果
# --------------------------------------------------------------------------


@dataclass
class EngineOptions:
    """エンジンの実行オプション。"""

    results_root: Path = Path(const.DEFAULT_RESULTS_DIRNAME)
    db_path: Path = Path(const.DEFAULT_DB_FILENAME)
    fail_fast: bool = False
    #: 実行後に生データを DB へ取り込むか(§5.2 の経路。通常 True)
    import_to_db: bool = True


@dataclass
class EngineResult:
    """1 回の ``benchman run`` の結果。"""

    suite_name: str
    results_dir: Path
    runs: list[TestRunResult] = field(default_factory=list)
    import_stats: ImportStats | None = None

    @property
    def ok_count(self) -> int:
        return sum(1 for run in self.runs if run.status != MeasurementStatus.FAILED)

    @property
    def failed_count(self) -> int:
        return sum(1 for run in self.runs if run.status == MeasurementStatus.FAILED)


@dataclass
class PlanInfo:
    """``benchman plan`` 用の情報。"""

    suite_name: str
    tool: str
    strategy: str
    points: list[MatrixPoint]
    env_order: list[str]
    env_switches: int
    env_switches_naive: int
    points_per_run: int
    seconds_per_point: int

    @property
    def total_seconds(self) -> int:
        return len(self.points) * self.points_per_run * self.seconds_per_point


# --------------------------------------------------------------------------
# エンジン
# --------------------------------------------------------------------------


class Engine:
    """テスト実行のオーケストレーション。"""

    def __init__(
        self,
        config: Config,
        options: EngineOptions | None = None,
        *,
        server_executors: list[RemoteExecutor] | None = None,
        storage_executor: RemoteExecutor | None = None,
        driver: BenchDriver | None = None,
    ):
        self.config = config
        self.options = options or EngineOptions()
        self.driver = driver or self._create_driver()
        #: テストから差し替えられるように注入可能にしておく(実機不要のため)
        self._injected_servers = server_executors
        self._injected_storage = storage_executor
        self.server_executors: list[RemoteExecutor] = []
        self.storage_executor: RemoteExecutor | None = None

    # -- 準備 -------------------------------------------------------------
    def _create_driver(self) -> BenchDriver:
        driver_cls = get_driver_class(self.config.tool)
        return driver_cls(config=self.config, server=self.config.primary_server())

    @property
    def timing(self) -> Timing:
        timing = self.config.test.timing
        return Timing(
            duration_sec=timing.duration_sec,
            warmup_sec=timing.warmup_sec,
            interval_sec=timing.interval_sec,
        )

    def _build_executors(self) -> None:
        driver_cls = type(self.driver)
        if self._injected_servers is not None:
            self.server_executors = list(self._injected_servers)
        else:
            # 実行先の種類(SSH / モック)はドライバが決める。エンジンは知らない
            self.server_executors = [
                driver_cls.create_executor(server) for server in self.config.servers.servers
            ]
        if self._injected_storage is not None:
            self.storage_executor = self._injected_storage
        elif self.config.storage.storage.management is not None:
            self.storage_executor = driver_cls.create_management_executor(
                self.config.storage.storage.management, name=self.config.storage.storage.name
            )

    # -- 計画 -------------------------------------------------------------
    def plan(self) -> PlanInfo:
        """dry-run 情報(条件展開・適用順・推定所要時間)を作る(§6.4)。"""
        points = expand_matrix(self.config)
        optimized, naive = count_env_switches(points)
        strategy_name = self.config.test.strategy.type
        strategy = self.driver.create_strategy(
            strategy_name,
            StrategySpec.from_config(
                self.config.test.strategy,
                max_load_iops=self.config.test.strategy.max_load.manual_iops or 0,
            ),
        )
        points_per_run = strategy.estimated_points()
        if self.driver.requires_max_load(strategy_name) and (
            self.config.test.strategy.max_load.mode == "measure"
        ):
            points_per_run += 1
        return PlanInfo(
            suite_name=self.config.suite_name,
            tool=self.config.tool,
            strategy=strategy_name,
            points=points,
            env_order=[name for name, _ in self.config.env_params_in_order()],
            env_switches=optimized,
            env_switches_naive=naive,
            points_per_run=max(points_per_run, 1),
            seconds_per_point=self.timing.total_sec,
        )

    # -- 実行 -------------------------------------------------------------
    async def run(self) -> EngineResult:
        """全条件を実行し、生データ保存 → DB 取り込みまで行う。"""
        started_at = datetime.now()
        points = expand_matrix(self.config)
        logger.info(
            t(
                "engine.suite_start",
                suite=self.config.suite_name,
                count=len(points),
                tool=self.config.tool,
            )
        )

        writer = ResultsWriter(
            root=Path(self.options.results_root)
            / suite_dirname(self.config.suite_name, started_at),
            tool=self.config.tool,
            suite_name=self.config.suite_name,
            created_at=started_at,
        )
        self._write_manifest(writer, points)
        logger.info(t("engine.results_dir", path=writer.root))

        self._build_executors()
        applier = EnvParamApplier(
            definitions=self.config.test.env_params,
            executors={
                TARGET_SERVER: self.server_executors,
                TARGET_STORAGE: [self.storage_executor] if self.storage_executor else [],
            },
            evidence_writer=writer.write_env_evidence,
        )

        result = EngineResult(suite_name=self.config.suite_name, results_dir=writer.root)
        try:
            for executor in self.server_executors:
                await executor.connect()
            for point in points:
                run_result = await self._run_condition(point, len(points), writer, applier)
                result.runs.append(run_result)
                if run_result.status == MeasurementStatus.FAILED and self.options.fail_fast:
                    logger.error(t("engine.fail_fast"))
                    break
        finally:
            for executor in self.server_executors:
                await executor.close()
            if self.storage_executor is not None:
                await self.storage_executor.close()

        if self.options.import_to_db:
            with Store(self.options.db_path) as store:
                result.import_stats = import_results(writer.root, store)

        logger.info(
            t(
                "engine.suite_done",
                suite=self.config.suite_name,
                ok=result.ok_count,
                failed=result.failed_count,
                path=writer.root,
            )
        )
        return result

    def _write_manifest(self, writer: ResultsWriter, points: list[MatrixPoint]) -> None:
        strategy = self.config.test.strategy
        writer.write_manifest(
            {
                "test_suite": {
                    "name": self.config.suite_name,
                    "description": self.config.test.test.description,
                },
                "strategy": {
                    "type": strategy.type,
                    "max_load_mode": strategy.max_load.mode,
                    "latency_threshold_ms": strategy.max_load.latency_threshold_ms,
                },
                "timing": {
                    "duration_sec": self.timing.duration_sec,
                    "warmup_sec": self.timing.warmup_sec,
                    "interval_sec": self.timing.interval_sec,
                },
                "environment": {
                    "storage": self.config.storage.storage.name,
                    "servers": [server.name for server in self.config.servers.servers],
                    "conditions_count": len(points),
                },
                "tool_params": {k: v for k, v in self.config.tool_params.items()},
            }
        )
        writer.copy_config_files(self.config.paths.all())
        writer.copy_params_spec(type(self.driver).params_spec_path())

    async def _run_condition(
        self,
        point: MatrixPoint,
        total: int,
        writer: ResultsWriter,
        applier: EnvParamApplier,
    ) -> TestRunResult:
        """1 テスト条件を実行する。失敗しても次の条件へ進めるよう例外を閉じ込める。"""
        logger.info(t("engine.run_start", index=point.index, total=total, conditions=point.label()))
        run_result = TestRunResult(
            index=point.index, conditions=point.conditions, started_at=datetime.now()
        )
        writer.create_run_dir(point.index)
        run_result.raw_dir = writer.layout.run_dir(point.index)

        try:
            await applier.apply_all(point.env_values)
            await self._measure_condition(point, writer, run_result)
        except BenchmanError as exc:
            run_result.status = MeasurementStatus.FAILED
            run_result.error = str(exc)
            logger.error(t("engine.run_failed", index=point.index, total=total, reason=exc))
        else:
            logger.info(
                t(
                    "engine.run_done",
                    index=point.index,
                    total=total,
                    points=len(run_result.measurements),
                )
            )
        run_result.finished_at = datetime.now()
        if run_result.status != MeasurementStatus.FAILED and any(
            m.status == MeasurementStatus.FAILED for m in run_result.measurements
        ):
            run_result.status = MeasurementStatus.WARN
        writer.write_run_meta(
            index=point.index,
            conditions=point.conditions,
            status=run_result.status,
            started_at=run_result.started_at,
            finished_at=run_result.finished_at,
            error=run_result.error,
        )
        return run_result

    async def _measure_condition(
        self, point: MatrixPoint, writer: ResultsWriter, run_result: TestRunResult
    ) -> None:
        """最大負荷計測(必要なら)→ 戦略ループ。"""
        strategy_name = self.config.test.strategy.type
        history: list[MeasurementResult] = []
        seq = 0
        max_load: MaxLoadOutcome | None = None

        if self.driver.requires_max_load(strategy_name):
            measurer = MaxLoadMeasurer.from_config(self.config.test.strategy.max_load)
            if measurer.needs_measurement:
                logger.info(t("engine.measure_max_start"))
                seq += 1
                measured = await self._measure_point(point, None, seq, writer)
                history.append(measured)
                run_result.measurements.append(measured)
                max_load = measurer.resolve(measured)  # 負荷不十分なら例外
                logger.info(
                    t(
                        "engine.max_load_adopted",
                        iops=max_load.iops,
                        latency=max_load.latency_ms,
                        threshold=measurer.latency_threshold_ms,
                    )
                )
            else:
                max_load = measurer.resolve(None)
                logger.info(t("engine.max_load_manual", iops=int(max_load.iops)))

        strategy = self.driver.create_strategy(
            strategy_name,
            StrategySpec.from_config(
                self.config.test.strategy,
                max_load_iops=max_load.iops if max_load else None,
            ),
        )

        while True:
            target = strategy.next_target(history)
            if target is None:
                logger.info(t("engine.strategy_stopped"))
                break
            seq += 1
            measured = await self._measure_point(point, target, seq, writer)
            history.append(measured)
            run_result.measurements.append(measured)

    async def _measure_point(
        self,
        point: MatrixPoint,
        target_iops: int | None,
        seq: int,
        writer: ResultsWriter,
    ) -> MeasurementResult:
        """1 測定点。失敗時は 1 回リトライし、再失敗ならその点を失敗として記録する(§4.5)。"""
        timing = self.timing
        logger.info(
            t(
                "engine.measure_start",
                target="max" if target_iops is None else target_iops,
                warmup=timing.warmup_sec,
                duration=timing.duration_sec,
            )
        )
        result = MeasurementResult(seq=seq, target_iops=target_iops, started_at=datetime.now())
        config_text = self.driver.generate_config(point.conditions, target_iops)
        remote = self.server_executors[0]
        command = ""
        exit_code = 0

        for attempt in range(MEASUREMENT_RETRIES + 1):
            measurement_dir = writer.create_measurement_dir(point.index, seq)
            try:
                raw = await self.driver.run(remote, config_text, timing, measurement_dir)
                metrics = self.driver.parse(raw)
                result.metrics = {metric.name: metric.value for metric in metrics}
                result.raw_dir = measurement_dir
                result.status = MeasurementStatus.OK
                result.error = ""
                command = raw.command
                exit_code = raw.exit_code
                break
            except (MeasurementError, RemoteError) as exc:
                result.error = str(exc)
                exit_code = 1
                if attempt < MEASUREMENT_RETRIES:
                    logger.warning(
                        t(
                            "engine.measure_retry",
                            target="max" if target_iops is None else target_iops,
                            reason=exc,
                        )
                    )
                    continue
                result.status = MeasurementStatus.FAILED
                logger.error(
                    t(
                        "engine.measure_failed",
                        target="max" if target_iops is None else target_iops,
                        reason=exc,
                    )
                )

        result.finished_at = datetime.now()
        if result.status != MeasurementStatus.FAILED:
            if (
                target_iops is not None
                and result.iops > 0
                and result.iops < target_iops * IOPS_SHORTFALL_RATIO
            ):
                # 飽和領域。実測値で記録し警告する(判定・グラフも実測 IOPS を使う)
                result.status = MeasurementStatus.WARN
                logger.warning(
                    t(
                        "engine.iops_shortfall",
                        target=target_iops,
                        actual=result.iops,
                        pct=result.iops / target_iops * 100.0,
                    )
                )
            logger.info(
                t(
                    "engine.measure_done",
                    target="max" if target_iops is None else target_iops,
                    iops=result.iops,
                    latency=result.latency_avg,
                )
            )

        writer.write_measurement_meta(
            run_index=point.index,
            seq=seq,
            target_iops=target_iops,
            status=result.status,
            started_at=result.started_at,
            finished_at=result.finished_at,
            exit_code=exit_code,
            command=command,
            error=result.error,
        )
        return result


__all__ = [
    "Engine",
    "EngineOptions",
    "EngineResult",
    "MatrixPoint",
    "PlanInfo",
    "count_env_switches",
    "expand_matrix",
    "MaxLoadInsufficientError",
]
