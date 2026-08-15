"""生データ ↔ DB の往復(§5.2、受け入れ基準 3)。"""

from __future__ import annotations

import pytest

from benchmanager import metrics as M
from benchmanager.config import load_config
from benchmanager.engine import Engine, EngineOptions
from benchmanager.errors import StoreError
from benchmanager.store.importer import import_results
from benchmanager.store.store import Store
from benchmanager.tomlwriter import dumps

TOML_TEXT = """
[test]
tool = "mock"
test_suite = "roundtrip"

[strategy]
type = "range"
start_iops = 10000
end_iops = 30000
step_iops = 10000

[timing]
duration_sec = 2
warmup_sec = 0

[tool_params]
max_iops = 100000

[matrix]
read_pct = [100, 0]
"""


@pytest.fixture
async def executed(make_config_dir, tmp_path):
    config = load_config(make_config_dir(TOML_TEXT))
    engine = Engine(
        config,
        EngineOptions(results_root=tmp_path / "results", db_path=tmp_path / "a.sqlite"),
    )
    result = await engine.run()
    return result


def snapshot(store: Store, name: str):
    """比較用に DB の内容を素の値へ落とす(id は除く)。"""
    suite = store.load_suite(name)
    assert suite is not None
    return [
        (
            run.conditions,
            run.status,
            [
                (m.seq, m.target_iops, m.status, sorted(m.metrics.items()))
                for m in run.measurements
            ],
        )
        for run in suite.runs
    ]


async def test_run_imports_into_db(executed, tmp_path):
    with Store(tmp_path / "a.sqlite") as store:
        counts = store.counts()
    assert counts["test_suite"] == 1
    assert counts["test_run"] == 2
    assert counts["measurement"] == 6
    assert counts["metric"] > 0
    assert counts["timeseries_metric"] > 0


async def test_reimport_rebuilds_identical_db(executed, tmp_path):
    """パーサー修正後の再パースを想定: 何度取り込んでも同じ結果になること。"""
    with Store(tmp_path / "a.sqlite") as store:
        original = snapshot(store, "roundtrip")

    # 別 DB へ生データから再構築
    with Store(tmp_path / "b.sqlite") as store:
        import_results(executed.results_dir, store)
        rebuilt = snapshot(store, "roundtrip")
    assert rebuilt == original

    # 同じ DB へ再取り込みしても重複しない
    with Store(tmp_path / "a.sqlite") as store:
        import_results(executed.results_dir, store)
        assert snapshot(store, "roundtrip") == original
        assert store.counts()["test_run"] == 2


async def test_report_is_identical_after_reimport(executed, tmp_path):
    from benchmanager.report.html import collect_series

    with Store(tmp_path / "a.sqlite") as store:
        first = collect_series(store.load_suite("roundtrip"))
    with Store(tmp_path / "c.sqlite") as store:
        import_results(executed.results_dir, store)
        second = collect_series(store.load_suite("roundtrip"))

    assert [s.title for s in first] == [s.title for s in second]
    assert [
        [(p.target_iops, round(p.iops, 6), round(p.latency_avg, 6), p.status) for p in s.points]
        for s in first
    ] == [
        [(p.target_iops, round(p.iops, 6), round(p.latency_avg, 6), p.status) for p in s.points]
        for s in second
    ]


async def test_raw_directory_layout(executed):
    root = executed.results_dir
    assert (root / "manifest.toml").is_file()
    run_dir = root / "raw" / "mock" / "run-0001"
    assert (run_dir / "run.toml").is_file()
    measurement_dir = run_dir / "m-0001"
    assert (measurement_dir / "measurement.toml").is_file()
    assert (measurement_dir / "result.json").is_file()


async def test_metrics_are_stored_vertically(executed, tmp_path):
    with Store(tmp_path / "a.sqlite") as store:
        rows = store.conn.execute(
            "SELECT DISTINCT name, unit, source FROM metric ORDER BY name"
        ).fetchall()
    names = {row["name"] for row in rows}
    assert {M.IOPS, M.LATENCY_AVG, M.THROUGHPUT_MBPS, M.READ_IOPS} <= names
    assert all(row["source"] == "mock" for row in rows)
    units = {row["name"]: row["unit"] for row in rows}
    assert units[M.LATENCY_AVG] == "ms"


async def test_timeseries_readback(executed, tmp_path):
    with Store(tmp_path / "a.sqlite") as store:
        suite = store.load_suite("roundtrip")
        measurement_id = suite.runs[0].measurements[0].id
        points = store.timeseries(measurement_id, M.IOPS)
    assert points and all(p.name == M.IOPS for p in points)
    assert [p.ts for p in points] == sorted(p.ts for p in points)


def test_import_without_manifest_raises(tmp_path):
    (tmp_path / "empty").mkdir()
    with Store(tmp_path / "x.sqlite") as store:
        with pytest.raises(StoreError):
            import_results(tmp_path / "empty", store)


def test_toml_writer_roundtrip():
    import tomllib

    data = {
        "manifest": {"version": 1, "tool": "mock", "flag": True},
        "conditions": {"read_pct": 100, "block_size": "4k", "ratio": 1.5},
        "list": {"values": [1, 2, 3], "names": ["a", 'b"c']},
        "skipped": {"nothing": None, "kept": "x"},
    }
    parsed = tomllib.loads(dumps(data))
    assert parsed["manifest"]["version"] == 1
    assert parsed["manifest"]["flag"] is True
    assert parsed["conditions"]["block_size"] == "4k"
    assert parsed["list"]["names"] == ["a", 'b"c']
    assert "nothing" not in parsed["skipped"]  # None はキーごと省略
    assert parsed["skipped"]["kept"] == "x"
