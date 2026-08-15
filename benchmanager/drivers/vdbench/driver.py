"""vdbench ドライバ(§4.1, §9)。

* vdbench の curve 機能は使わない。本ツールが 1 測定点ずつ ``iorate`` を指定する
* SSH 経由でリモートの負荷サーバ上で実行する(parmfile 転送 → 実行 → 出力回収 → パース)
* ``target_iops=None`` は ``iorate=max``(最大負荷計測)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ...errors import DriverError, MeasurementError
from ...i18n import t
from ...remote.base import RemoteExecutor
from ...strategy.base import MeasurementStrategy, StrategySpec
from ...strategy.registry import GENERIC_STRATEGIES, is_generic
from ...types import Metric, RawResult, TimeseriesPoint, Timing
from ..base import BenchDriver
from . import parser as vdparser
from .strategies import (
    VDBENCH_STRATEGIES,
    VDBENCH_STRATEGY_NAMES,
    create_vdbench_strategy,
)

logger = logging.getLogger(__name__)

#: 実行タイムアウトに加える余裕(秒)
TIMEOUT_MARGIN_SEC = 300

PARMFILE_NAME = "parmfile"
STDOUT_NAME = "stdout.log"
OUTPUT_DIRNAME = "output"


class VdbenchDriver(BenchDriver):
    """vdbench を SSH 経由で実行するドライバ。"""

    tool_name = "vdbench"
    supports_max_load = True

    # -- 測定戦略 ---------------------------------------------------------
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

    # -- parmfile 生成 ----------------------------------------------------
    @property
    def timing(self) -> Timing:
        if self.config is not None:
            timing = self.config.test.timing
            return Timing(
                duration_sec=timing.duration_sec,
                warmup_sec=timing.warmup_sec,
                interval_sec=timing.interval_sec,
            )
        return Timing()

    def generate_config(self, conditions: dict[str, Any], target_iops: int | None) -> str:
        """1 測定点分の parmfile を生成する。

        parmfile へのマッピングはこのコードの責務(TOML でテンプレート化しない。§7)。
        """
        if self.config is None:
            raise DriverError(t("driver.parse_failed", path="-", message="config is required"))
        servers = self.config.servers.servers
        storage = self.config.storage.storage
        timing = self.timing
        raw_params = self.config.test.raw_tool_params

        def value(name: str, default: Any = None) -> Any:
            return conditions.get(name, default)

        lines: list[str] = [
            f"* {t('app.name')} generated parmfile",
            f"* generated_at={datetime.now().isoformat(timespec='seconds')}",
            f"* conditions={conditions}",
            "",
            "* Dedup / Compression",
            f"dedupratio={value('dedupratio', 1)}",
            f"dedupunit={value('dedupunit', '32k')}",
            f"compratio={value('compratio', 1)}",
            "",
            "* Host Definitions",
            f"hd=default,shell={value('shell', 'ssh')},jvms={value('jvms', 16)}",
        ]
        for index, server in enumerate(servers, start=1):
            lines.append(f"hd=hd{index},system={server.host},user={server.user}")

        openflags = value("openflags", "o_direct")
        sd_default = f"sd=default,size={value('file_size', '256g')}"
        if openflags and openflags != "none":
            sd_default += f",openflag={openflags}"
        if value("hitarea"):
            sd_default += f",hitarea={value('hitarea')}"
        lines += ["", "* Storage Definitions", sd_default]

        luns = list(storage.luns)
        if not luns:
            raise DriverError(t("driver.no_luns", file="storage.toml"))
        files_per_host = int(value("files_per_host", 1) or 1)
        for host_index, _server in enumerate(servers, start=1):
            for file_index in range(files_per_host):
                lun = luns[file_index % len(luns)]
                lines.append(f"sd=sd{host_index}-{file_index + 1},host=hd{host_index},lun={lun}")

        workload = [
            "wd=wd1",
            "sd=*",
            f"seekpct={_number(value('seekpct', 100))}",
            f"xfersize={value('block_size', '4k')}",
            f"rdpct={_number(value('read_pct', 100))}",
        ]
        if value("rhpct"):
            workload.append(f"rhpct={_number(value('rhpct'))}")
        if value("whpct"):
            workload.append(f"whpct={_number(value('whpct'))}")
        if value("range_pct"):
            workload.append(f"range=(0,{_number(value('range_pct'))})")
        lines += ["", "* Workload Definitions", ",".join(workload)]

        iorate = "max" if target_iops is None else str(int(target_iops))
        lines += [
            "",
            "* Run Definitions",
            f"rd=default,warmup={timing.warmup_sec},elapsed={timing.duration_sec}"
            f",interval={timing.interval_sec}",
            f"rd=rd1,wd=wd1,iorate={iorate},threads={_number(value('threads', 32))}",
        ]

        if raw_params:
            lines += ["", "* Raw pass-through parameters ([tool_params.raw])"]
            lines += [f"{key}={val}" for key, val in raw_params.items()]

        return "\n".join(lines) + "\n"

    # -- 実行 -------------------------------------------------------------
    async def run(
        self,
        remote: RemoteExecutor,
        config: str,
        timing: Timing,
        output_dir: Path,
    ) -> RawResult:
        """parmfile 転送 → vdbench 実行 → 出力回収。"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / PARMFILE_NAME).write_text(config, encoding="utf-8")

        workdir = self._remote_workdir(output_dir)
        remote_parmfile = f"{workdir}/{PARMFILE_NAME}"
        remote_output = f"{workdir}/{OUTPUT_DIRNAME}"

        await remote.mkdir(workdir)
        await remote.put_text(config, remote_parmfile)

        command = f"{self._vdbench_binary()} -f {remote_parmfile} -o {remote_output}"
        result = await remote.run(command, timeout=timing.total_sec + TIMEOUT_MARGIN_SEC)
        (output_dir / STDOUT_NAME).write_text(result.output, encoding="utf-8")

        # 失敗時も回収して原因調査できるようにする
        try:
            await remote.get_dir(remote_output, output_dir)
        except Exception as exc:  # 回収失敗そのものは測定失敗として扱う
            if result.ok:
                raise MeasurementError(
                    t("driver.parse_failed", path=remote_output, message=exc)
                ) from exc

        if not result.ok:
            raise MeasurementError(
                t("driver.exit_nonzero", tool=self.tool_name, rc=result.exit_code)
            )
        return RawResult(
            directory=output_dir,
            tool=self.tool_name,
            exit_code=result.exit_code,
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _vdbench_binary(self) -> str:
        path = getattr(self.server, "vdbench_path", None)
        return f"{path.rstrip('/')}/vdbench" if path else "vdbench"

    def _remote_workdir(self, output_dir: Path) -> str:
        base = getattr(self.server, "workdir", "/tmp/benchman") or "/tmp/benchman"
        return f"{base.rstrip('/')}/{output_dir.parent.name}-{output_dir.name}"

    # -- パース -----------------------------------------------------------
    def parse(self, raw: RawResult) -> list[Metric]:
        flatfile = vdparser.find_output_file(raw.directory, vdparser.FLATFILE_NAME)
        if flatfile is None:
            raise MeasurementError(
                t("driver.flatfile_missing", path=Path(raw.directory) / vdparser.FLATFILE_NAME)
            )
        try:
            metrics = vdparser.parse_flatfile_metrics(flatfile.read_text(encoding="utf-8", errors="replace"))
        except vdparser.VdbenchParseError as exc:
            raise MeasurementError(t("driver.parse_failed", path=flatfile, message=exc)) from exc

        histogram = vdparser.find_output_file(raw.directory, vdparser.HISTOGRAM_NAME)
        if histogram is not None:
            metrics += vdparser.parse_histogram_metrics(
                histogram.read_text(encoding="utf-8", errors="replace")
            )
        return metrics

    def parse_timeseries(self, raw: RawResult) -> list[TimeseriesPoint]:
        flatfile = vdparser.find_output_file(raw.directory, vdparser.FLATFILE_NAME)
        if flatfile is None:
            return []
        try:
            return vdparser.parse_flatfile_timeseries(
                flatfile.read_text(encoding="utf-8", errors="replace")
            )
        except vdparser.VdbenchParseError:
            return []


def _number(value: Any) -> str:
    """``100.0`` を ``100`` として書き出す(parmfile を読みやすくするため)。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)
