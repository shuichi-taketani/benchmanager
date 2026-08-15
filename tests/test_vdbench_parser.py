"""vdbench パーサーの単体テスト。

実測の flatfile / histogram をフィクスチャとして使う(§10)。
期待値は同じ測定の result.csv(vdbench 実行環境が出力したもの)と一致すること。
"""

from __future__ import annotations

import csv

import pytest

from benchmanager import metrics as M
from benchmanager.drivers.vdbench import parser as vdparser

from .conftest import VDBENCH_OUTPUT


@pytest.fixture
def expected_row() -> dict[str, str]:
    with (VDBENCH_OUTPUT / "expected_result.csv").open(encoding="utf-8") as fh:
        return next(iter(csv.DictReader(fh)))


def metrics_by_name(metrics) -> dict[str, float]:
    return {metric.name: metric.value for metric in metrics}


def test_summary_metrics_match_reference_csv(flatfile_text, expected_row):
    values = metrics_by_name(vdparser.parse_flatfile_metrics(flatfile_text))
    assert values[M.IOPS] == pytest.approx(float(expected_row["iops"]), rel=1e-6)
    assert values[M.THROUGHPUT_MBPS] == pytest.approx(float(expected_row["throughput"]), rel=1e-4)
    assert values[M.LATENCY_AVG] == pytest.approx(float(expected_row["resp_time"]), rel=1e-4)
    # result.csv 側は小数 2 桁に丸められているため許容誤差を緩める
    assert values[M.LATENCY_MAX] == pytest.approx(float(expected_row["read_max"]), rel=1e-4)
    assert values[M.LATENCY_STDDEV] == pytest.approx(float(expected_row["resp_stddev"]), rel=1e-4)
    assert values[M.QUEUE_DEPTH] == pytest.approx(float(expected_row["queue_depth"]), rel=1e-4)
    assert values[M.CPU_USED_PCT] == pytest.approx(float(expected_row["cpu_sys+u"]), rel=1e-3)
    assert values[M.XFERSIZE_BYTES] == pytest.approx(float(expected_row["blocksize"]))


def test_read_write_iops_are_derived(flatfile_text):
    values = metrics_by_name(vdparser.parse_flatfile_metrics(flatfile_text))
    assert values[M.READ_PCT] == 100.0
    assert values[M.READ_IOPS] == pytest.approx(values[M.IOPS])
    assert values[M.WRITE_IOPS] == 0.0


def test_summary_row_is_the_avg_row(flatfile_text):
    rows = vdparser.parse_flatfile(flatfile_text)
    summary = vdparser.summary_row(rows)
    assert summary is not None
    assert summary.interval.startswith("avg_")
    assert all(not row.is_summary for row in vdparser.interval_rows(rows))


def test_timeseries_uses_elapsed_seconds(flatfile_text):
    points = vdparser.parse_flatfile_timeseries(flatfile_text)
    assert points
    iops_points = [p for p in points if p.name == M.IOPS]
    assert iops_points[0].ts == 0.0
    assert iops_points[-1].ts > 0
    assert all(p.ts >= 0 for p in iops_points)
    # 単調増加(時刻順)
    assert iops_points == sorted(iops_points, key=lambda p: p.ts)


def test_missing_header_raises():
    with pytest.raises(vdparser.VdbenchParseError):
        vdparser.parse_flatfile("<title>x</title><pre>\n* only comments\n")


def test_missing_summary_row_raises(flatfile_text):
    without_summary = "\n".join(
        line for line in flatfile_text.splitlines() if " avg_" not in line
    )
    with pytest.raises(vdparser.VdbenchParseError):
        vdparser.parse_flatfile_metrics(without_summary)


def test_na_values_become_none(flatfile_text):
    rows = vdparser.parse_flatfile(flatfile_text)
    # version 列は 'n/a'
    assert rows[0].number("version") is None


def test_histogram_percentiles(histogram_text):
    values = metrics_by_name(vdparser.parse_histogram_metrics(histogram_text))
    assert set(values) == {M.LATENCY_P50, M.LATENCY_P90, M.LATENCY_P95, M.LATENCY_P99}
    assert values[M.LATENCY_P50] < values[M.LATENCY_P90] < values[M.LATENCY_P95]
    assert values[M.LATENCY_P95] < values[M.LATENCY_P99]
    # 平均 5.3ms の分布なので p50 は 4-8ms のバケットに入る
    assert 4.0 <= values[M.LATENCY_P50] <= 8.0


def test_histogram_empty_input():
    assert vdparser.parse_histogram("") == []
    assert vdparser.percentile_from_histogram([], 95.0) is None


def test_find_output_file_searches_recursively(tmp_path):
    nested = tmp_path / "output" / "deep"
    nested.mkdir(parents=True)
    (nested / vdparser.FLATFILE_NAME).write_text("x", encoding="utf-8")
    found = vdparser.find_output_file(tmp_path, vdparser.FLATFILE_NAME)
    assert found is not None and found.parent == nested
