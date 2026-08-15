"""SQLite ストレージ層。

インターフェースは意図的に狭くしてある(§13: 時系列が増えたら Parquet + DuckDB へ
逃がせるようにするため)。上位層は SQL を直接書かず、このクラスの API のみを使うこと。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import Metric, TimeseriesPoint
from .schema import DDL, SCHEMA_VERSION


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


# --------------------------------------------------------------------------
# 読み出し用のビューモデル
# --------------------------------------------------------------------------


@dataclass
class MeasurementRow:
    id: int
    seq: int
    target_iops: int | None
    status: str
    started_at: str | None
    finished_at: str | None
    error: str
    metrics: dict[str, float] = field(default_factory=dict)

    def metric(self, name: str, source: str = "") -> float | None:
        if source:
            return self.metrics.get(f"{source}:{name}")
        # source を省略した場合は最初に見つかったものを返す
        if name in self.metrics:
            return self.metrics[name]
        for key, value in self.metrics.items():
            if key.endswith(":" + name):
                return value
        return None


@dataclass
class RunRow:
    id: int
    tool: str
    conditions: dict[str, Any]
    status: str
    started_at: str | None
    finished_at: str | None
    raw_dir: str
    error: str
    seq: int
    measurements: list[MeasurementRow] = field(default_factory=list)


@dataclass
class SuiteData:
    id: int
    name: str
    created_at: str
    description: str
    results_dir: str
    runs: list[RunRow] = field(default_factory=list)

    def measurement_count(self) -> int:
        return sum(len(run.measurements) for run in self.runs)


# --------------------------------------------------------------------------
# ストア本体
# --------------------------------------------------------------------------


class Store:
    """結果 DB へのアクセス。``with Store(path) as store:`` で使う。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL)
        if self.conn.execute("SELECT COUNT(*) FROM schema_info").fetchone()[0] == 0:
            self.conn.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
        self.conn.commit()

    # -- ライフサイクル ---------------------------------------------------
    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- 書き込み ---------------------------------------------------------
    def create_suite(
        self,
        name: str,
        created_at: datetime | str,
        description: str = "",
        results_dir: str = "",
    ) -> int:
        """テストスイートを作成する(同じ name + results_dir があれば作り直す)。"""
        self.delete_suite(name, results_dir)
        cur = self.conn.execute(
            "INSERT INTO test_suite(name, created_at, description, results_dir) VALUES (?,?,?,?)",
            (name, _iso(created_at), description, results_dir),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def delete_suite(self, name: str, results_dir: str = "") -> bool:
        """同一 name + results_dir のスイートを削除する(再インポート用)。"""
        cur = self.conn.execute(
            "SELECT id FROM test_suite WHERE name = ? AND results_dir = ?", (name, results_dir)
        )
        rows = cur.fetchall()
        for row in rows:
            self.conn.execute("DELETE FROM test_suite WHERE id = ?", (row["id"],))
        self.conn.commit()
        return bool(rows)

    def add_run(
        self,
        suite_id: int,
        tool: str,
        conditions: dict[str, Any],
        status: str,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        raw_dir: str = "",
        error: str = "",
        seq: int = 0,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO test_run(test_suite_id, tool, conditions_json, status,
                                 started_at, finished_at, raw_dir, error, seq)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                suite_id,
                tool,
                json.dumps(conditions, ensure_ascii=False, sort_keys=True),
                status,
                _iso(started_at),
                _iso(finished_at),
                raw_dir,
                error,
                seq,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_measurement(
        self,
        run_id: int,
        seq: int,
        target_iops: int | None,
        status: str,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        raw_dir: str = "",
        error: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO measurement(test_run_id, seq, target_iops, status,
                                    started_at, finished_at, raw_dir, error)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (run_id, seq, target_iops, status, _iso(started_at), _iso(finished_at), raw_dir, error),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_metrics(self, measurement_id: int, metrics: Iterable[Metric]) -> int:
        rows = [(measurement_id, m.source, m.name, m.value, m.unit) for m in metrics]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO metric(measurement_id, source, name, value, unit) VALUES (?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def add_timeseries(self, measurement_id: int, points: Iterable[TimeseriesPoint]) -> int:
        rows = [(measurement_id, p.source, p.name, p.ts, p.value) for p in points]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO timeseries_metric(measurement_id, source, name, ts, value) VALUES (?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # -- 読み出し ---------------------------------------------------------
    def suite_names(self) -> list[str]:
        cur = self.conn.execute("SELECT DISTINCT name FROM test_suite ORDER BY name")
        return [row["name"] for row in cur.fetchall()]

    def load_suite(self, name: str) -> SuiteData | None:
        """スイート名から全 run / measurement / metric を読み出す。

        同名スイートが複数ディレクトリ分ある場合は最新(created_at 降順の先頭)を返す。
        """
        row = self.conn.execute(
            "SELECT * FROM test_suite WHERE name = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (name,),
        ).fetchone()
        if row is None:
            return None
        suite = SuiteData(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            description=row["description"],
            results_dir=row["results_dir"],
        )
        run_rows = self.conn.execute(
            "SELECT * FROM test_run WHERE test_suite_id = ? ORDER BY seq, id", (suite.id,)
        ).fetchall()
        for run_row in run_rows:
            run = RunRow(
                id=run_row["id"],
                tool=run_row["tool"],
                conditions=json.loads(run_row["conditions_json"]),
                status=run_row["status"],
                started_at=run_row["started_at"],
                finished_at=run_row["finished_at"],
                raw_dir=run_row["raw_dir"],
                error=run_row["error"],
                seq=run_row["seq"],
            )
            meas_rows = self.conn.execute(
                "SELECT * FROM measurement WHERE test_run_id = ? ORDER BY seq, id", (run.id,)
            ).fetchall()
            for meas_row in meas_rows:
                measurement = MeasurementRow(
                    id=meas_row["id"],
                    seq=meas_row["seq"],
                    target_iops=meas_row["target_iops"],
                    status=meas_row["status"],
                    started_at=meas_row["started_at"],
                    finished_at=meas_row["finished_at"],
                    error=meas_row["error"],
                )
                metric_rows = self.conn.execute(
                    "SELECT source, name, value FROM metric WHERE measurement_id = ?",
                    (measurement.id,),
                ).fetchall()
                for metric_row in metric_rows:
                    key = metric_row["name"]
                    if metric_row["source"] and metric_row["source"] != run.tool:
                        key = f"{metric_row['source']}:{metric_row['name']}"
                    measurement.metrics[key] = metric_row["value"]
                run.measurements.append(measurement)
            suite.runs.append(run)
        return suite

    def timeseries(self, measurement_id: int, name: str | None = None) -> list[TimeseriesPoint]:
        sql = "SELECT source, name, ts, value FROM timeseries_metric WHERE measurement_id = ?"
        args: list[Any] = [measurement_id]
        if name:
            sql += " AND name = ?"
            args.append(name)
        sql += " ORDER BY ts"
        return [
            TimeseriesPoint(name=row["name"], ts=row["ts"], value=row["value"], source=row["source"])
            for row in self.conn.execute(sql, args).fetchall()
        ]

    def counts(self) -> dict[str, int]:
        """テスト用: 各テーブルの行数。"""
        out = {}
        for table in ("test_suite", "test_run", "measurement", "metric", "timeseries_metric"):
            out[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return out
