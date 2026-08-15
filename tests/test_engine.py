"""エンジンの統合テスト(§10、受け入れ基準 1/3/4)。"""

from __future__ import annotations

import pytest

from benchmanager import metrics as M
from benchmanager.config import load_config
from benchmanager.engine import Engine, EngineOptions, count_env_switches, expand_matrix
from benchmanager.store.store import Store
from benchmanager.types import MeasurementStatus

RANGE_TOML = """
[test]
tool = "mock"
test_suite = "engine-range"

[strategy]
type = "range"
start_iops = 10000
end_iops = 40000
step_iops = 10000

[timing]
duration_sec = 5
warmup_sec = 1

[tool_params]
max_iops = 100000

[matrix]
read_pct = [100, 0]
"""

BISECT_TOML = """
[test]
tool = "mock"
test_suite = "engine-bisect"

[strategy]
type = "vdbench_bisect"
start_iops = 10000

[strategy.max_load]
mode = "measure"
latency_threshold_ms = 5.0

[strategy.params]
stop_delta_pct = 10
max_iterations = 6

[timing]
duration_sec = 5
warmup_sec = 1

[tool_params]
max_iops = 100000
k = 60000.0
"""

LINEAR_TOML = BISECT_TOML.replace("vdbench_bisect", "vdbench_linear_descent").replace(
    "engine-bisect", "engine-linear"
) + """
"""

ENV_TOML = """
[test]
tool = "mock"
test_suite = "engine-env"

[strategy]
type = "range"
start_iops = 10000
end_iops = 10000
step_iops = 10000

[timing]
duration_sec = 1
warmup_sec = 0

[matrix]
read_pct = [100, 0]
mount_opts = ["default", "nconnect"]

[env_params.mount_opts]
target = "server"
order = 1
values = ["default", "nconnect"]

[env_params.mount_opts.apply.default]
commands = ["mount -o vers=4.1 s:/v /mnt/bench"]
verify = "mount | grep -q vers=4.1"

[env_params.mount_opts.apply.nconnect]
commands = ["mount -o nconnect=8 s:/v /mnt/bench"]
verify = "mount | grep -q nconnect=8"
"""


def make_engine(directory, tmp_path, **options):
    config = load_config(directory)
    return Engine(
        config,
        EngineOptions(
            results_root=tmp_path / "results",
            db_path=tmp_path / "benchman.sqlite",
            **options,
        ),
    )


# --------------------------------------------------------------------------
# マトリクス展開
# --------------------------------------------------------------------------


def test_matrix_expansion_puts_env_params_outermost(make_config_dir):
    config = load_config(make_config_dir(ENV_TOML))
    points = expand_matrix(config)
    assert len(points) == 4
    # 環境パラメータ(外側)が最も遅く変化する
    assert [p.env_values["mount_opts"] for p in points] == [
        "default",
        "default",
        "nconnect",
        "nconnect",
    ]
    assert [p.tool_values["read_pct"] for p in points] == [100, 0, 100, 0]
    # conditions にはツールパラメータと環境パラメータが同列に入る(§5.1)
    assert points[0].conditions["mount_opts"] == "default"
    assert points[0].conditions["read_pct"] == 100
    assert "max_iops" in points[0].conditions


def test_env_switch_count_is_minimized(make_config_dir):
    config = load_config(make_config_dir(ENV_TOML))
    optimized, naive = count_env_switches(expand_matrix(config))
    assert optimized == 2   # default -> nconnect の 1 回 + 初回適用
    assert naive == 4


def test_plan_estimates_duration(make_config_dir):
    config = load_config(make_config_dir(RANGE_TOML))
    info = Engine(config).plan()
    assert len(info.points) == 2
    assert info.points_per_run == 4          # range の 4 点
    assert info.seconds_per_point == 6       # duration 5 + warmup 1
    assert info.total_seconds == 2 * 4 * 6


# --------------------------------------------------------------------------
# 実行(3 戦略)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "toml_text,expected_suite",
    [(RANGE_TOML, "engine-range"), (BISECT_TOML, "engine-bisect"), (LINEAR_TOML, "engine-linear")],
)
async def test_run_produces_curve_for_each_strategy(
    make_config_dir, tmp_path, toml_text, expected_suite
):
    engine = make_engine(make_config_dir(toml_text), tmp_path)
    result = await engine.run()

    assert result.suite_name == expected_suite
    assert result.failed_count == 0
    assert result.import_stats is not None and result.import_stats.measurements > 0

    with Store(tmp_path / "benchman.sqlite") as store:
        suite = store.load_suite(expected_suite)
        assert suite is not None
        for run in suite.runs:
            points = [m for m in run.measurements if m.status != MeasurementStatus.FAILED]
            assert len(points) >= 2
            # IOPS-Latency カーブになっている(IOPS が増えれば latency も増える)
            ordered = sorted(points, key=lambda m: m.metric(M.IOPS))
            latencies = [m.metric(M.LATENCY_AVG) for m in ordered]
            assert latencies == sorted(latencies)


async def test_max_load_measurement_is_the_first_point(make_config_dir, tmp_path):
    engine = make_engine(make_config_dir(BISECT_TOML), tmp_path)
    await engine.run()
    with Store(tmp_path / "benchman.sqlite") as store:
        suite = store.load_suite("engine-bisect")
        first = suite.runs[0].measurements[0]
        assert first.target_iops is None  # iorate=max
        assert first.seq == 1


async def test_insufficient_load_fails_only_that_condition(make_config_dir, tmp_path):
    """latency が閾値未満 → その条件を失敗にし、次の条件へ進む(§4.3)。"""
    toml_text = BISECT_TOML.replace(
        "latency_threshold_ms = 5.0", "latency_threshold_ms = 10000.0"
    ) + """
[matrix]
read_pct = [100, 0]
"""
    engine = make_engine(make_config_dir(toml_text), tmp_path)
    result = await engine.run()
    assert result.failed_count == 2
    assert all("負荷不十分" in run.error for run in result.runs)


async def test_manual_max_load_skips_measurement(make_config_dir, tmp_path):
    toml_text = BISECT_TOML.replace(
        'mode = "measure"', 'mode = "manual"\nmanual_iops = 80000'
    )
    engine = make_engine(make_config_dir(toml_text), tmp_path)
    result = await engine.run()
    assert result.failed_count == 0
    assert all(m.target_iops is not None for m in result.runs[0].measurements)


async def test_failed_point_is_retried_then_recorded(make_config_dir, tmp_path):
    """測定失敗は 1 回リトライ、再失敗で失敗として記録し次へ進む(§4.5)。"""
    toml_text = RANGE_TOML + """
[tool_params.raw]
"""
    toml_text = toml_text.replace("max_iops = 100000", "max_iops = 100000\nfail_at_iops = 20000")
    engine = make_engine(make_config_dir(toml_text), tmp_path)
    result = await engine.run()

    failed = [m for run in result.runs for m in run.measurements if m.status == MeasurementStatus.FAILED]
    assert len(failed) == 2  # 各条件の 20000 の点
    # 全体は止まらず、以降の点も測定されている
    assert all(len(run.measurements) == 4 for run in result.runs)
    assert all(run.status == MeasurementStatus.WARN for run in result.runs)


async def test_fail_fast_stops_after_first_failure(make_config_dir, tmp_path):
    toml_text = BISECT_TOML.replace(
        "latency_threshold_ms = 5.0", "latency_threshold_ms = 10000.0"
    ) + """
[matrix]
read_pct = [100, 0]
"""
    engine = make_engine(make_config_dir(toml_text), tmp_path, fail_fast=True)
    result = await engine.run()
    assert len(result.runs) == 1


async def test_iops_shortfall_is_recorded_as_warning(make_config_dir, tmp_path):
    """指定 IOPS に届かない場合は実測値で記録し警告する(§4.5)。"""
    toml_text = RANGE_TOML.replace("max_iops = 100000", "max_iops = 15000")
    engine = make_engine(make_config_dir(toml_text), tmp_path)
    result = await engine.run()
    statuses = [m.status for m in result.runs[0].measurements]
    assert MeasurementStatus.WARN in statuses
    warned = [m for m in result.runs[0].measurements if m.status == MeasurementStatus.WARN]
    assert all(m.iops < m.target_iops for m in warned)


# --------------------------------------------------------------------------
# 環境パラメータとの結合
# --------------------------------------------------------------------------


async def test_env_params_applied_with_minimal_switches(make_config_dir, tmp_path):
    engine = make_engine(make_config_dir(ENV_TOML), tmp_path)
    result = await engine.run()
    assert result.failed_count == 0

    executor = engine.server_executors[0]
    mounts = [cmd for cmd in executor.commands if cmd.startswith("mount -o")]
    assert mounts == [
        "mount -o vers=4.1 s:/v /mnt/bench",
        "mount -o nconnect=8 s:/v /mnt/bench",
    ]
    # 適用の証跡が raw/env/ に残る
    env_files = sorted((result.results_dir / "raw" / "env").glob("*.log"))
    assert len(env_files) == 2  # 実際に切り替えた 2 回分だけ(スキップ時は実行していない)
    assert "verify" in env_files[0].read_text(encoding="utf-8")


async def test_manifest_and_config_copies_are_saved(make_config_dir, tmp_path):
    directory = make_config_dir(RANGE_TOML)
    engine = make_engine(directory, tmp_path)
    result = await engine.run()

    manifest = result.results_dir / "manifest.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'tool = "mock"' in text
    for name in ("test.toml", "servers.toml", "storage.toml"):
        assert (result.results_dir / "config" / name).is_file()
    assert (result.results_dir / "params.toml").is_file()  # 再現性(§7)
