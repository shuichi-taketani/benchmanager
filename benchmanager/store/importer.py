"""結果ディレクトリ → SQLite の取り込み(§5.2)。

``benchman run`` も内部でこの経路を通るため、``benchman import <dir>`` で
いつでも同じ DB を再構築できる(パーサー修正後の再パース、他環境の結果取り込み)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..const import MEASUREMENT_META_FILENAME, RUN_META_FILENAME
from ..drivers.registry import available_tools, get_driver_class
from ..errors import StoreError
from ..i18n import t
from ..types import MeasurementStatus, RawResult
from .results import ResultsLayout, read_manifest, read_toml
from .store import Store

logger = logging.getLogger(__name__)


@dataclass
class ImportStats:
    """取り込み結果の件数。"""

    suite_id: int
    suite_name: str
    runs: int = 0
    measurements: int = 0
    metrics: int = 0
    timeseries: int = 0


def import_results(results_dir: str | Path, store: Store) -> ImportStats:
    """結果ディレクトリを読み、DB へ取り込む(同一スイートは置き換え)。"""
    root = Path(results_dir)
    if not root.is_dir():
        raise StoreError(t("importing.manifest_missing", path=root))
    logger.info(t("importing.start", path=root))

    manifest = read_manifest(root)
    manifest_section = manifest.get("manifest", {})
    tool = manifest_section.get("tool", "")
    suite_name = manifest.get("test_suite", {}).get("name", root.name)
    description = manifest.get("test_suite", {}).get("description", "")
    created_at = manifest_section.get("created_at") or datetime.now().isoformat(timespec="seconds")

    try:
        driver_cls = get_driver_class(tool)
    except KeyError as exc:
        raise StoreError(
            t("driver.unknown", tool=tool, available=", ".join(available_tools()))
        ) from exc
    driver = driver_cls()

    layout = ResultsLayout(root=root, tool=tool)
    replaced = store.delete_suite(suite_name, str(root))
    if replaced:
        logger.info(t("importing.replaced", suite=suite_name))
    suite_id = store.create_suite(
        name=suite_name, created_at=created_at, description=description, results_dir=str(root)
    )
    stats = ImportStats(suite_id=suite_id, suite_name=suite_name)

    for run_dir in layout.run_dirs():
        meta_path = run_dir / RUN_META_FILENAME
        if not meta_path.is_file():
            continue
        meta = read_toml(meta_path)
        run_meta = meta.get("run", {})
        conditions = meta.get("conditions", {})
        run_id = store.add_run(
            suite_id=suite_id,
            tool=tool,
            conditions=conditions,
            status=run_meta.get("status", MeasurementStatus.OK),
            started_at=run_meta.get("started_at"),
            finished_at=run_meta.get("finished_at"),
            raw_dir=str(run_dir),
            error=run_meta.get("error", ""),
            seq=int(run_meta.get("index", 0)),
        )
        stats.runs += 1

        for meas_dir in layout.measurement_dirs(run_dir):
            meas_meta_path = meas_dir / MEASUREMENT_META_FILENAME
            if not meas_meta_path.is_file():
                continue
            meas_meta = read_toml(meas_meta_path).get("measurement", {})
            is_max_load = bool(meas_meta.get("max_load", False))
            target_iops = meas_meta.get("target_iops")
            if is_max_load:
                target_iops = None
            status = meas_meta.get("status", MeasurementStatus.OK)
            measurement_id = store.add_measurement(
                run_id=run_id,
                seq=int(meas_meta.get("seq", 0)),
                target_iops=target_iops,
                status=status,
                started_at=meas_meta.get("started_at"),
                finished_at=meas_meta.get("finished_at"),
                raw_dir=str(meas_dir),
                error=meas_meta.get("error", ""),
            )
            stats.measurements += 1

            if status == MeasurementStatus.FAILED:
                # 失敗点は記録のみ(生出力が無い/壊れている可能性があるためパースしない)
                continue

            raw = RawResult(
                directory=meas_dir,
                tool=tool,
                exit_code=int(meas_meta.get("exit_code", 0)),
                command=meas_meta.get("command", ""),
            )
            metrics = driver.parse(raw)
            stats.metrics += store.add_metrics(measurement_id, metrics)
            stats.timeseries += store.add_timeseries(measurement_id, driver.parse_timeseries(raw))

    logger.info(
        t(
            "importing.done",
            runs=stats.runs,
            measurements=stats.measurements,
            metrics=stats.metrics,
        )
    )
    return stats
