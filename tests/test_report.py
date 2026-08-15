"""レポート生成(§11、受け入れ基準 1/3)。"""

from __future__ import annotations

import re

import pytest

from benchmanager import metrics as M
from benchmanager.errors import ReportError
from benchmanager.report.html import collect_series, build_figure, write_report
from benchmanager.store.store import MeasurementRow, RunRow, Store, SuiteData
from benchmanager.types import MeasurementStatus


def make_suite() -> SuiteData:
    suite = SuiteData(
        id=1, name="s", created_at="2026-07-08T14:30:00", description="", results_dir="/tmp/x"
    )
    for run_index, read_pct in enumerate([100, 0], start=1):
        run = RunRow(
            id=run_index,
            tool="mock",
            conditions={"read_pct": read_pct, "block_size": "4k"},
            status=MeasurementStatus.OK,
            started_at=None,
            finished_at=None,
            raw_dir="",
            error="",
            seq=run_index,
        )
        for seq, (target, iops, latency, status) in enumerate(
            [
                (10000, 10000.0, 1.0, MeasurementStatus.OK),
                (20000, 20000.0, 2.0, MeasurementStatus.OK),
                (30000, 25000.0, 8.0, MeasurementStatus.WARN),
                (40000, 0.0, 0.0, MeasurementStatus.FAILED),
            ],
            start=1,
        ):
            measurement = MeasurementRow(
                id=run_index * 100 + seq,
                seq=seq,
                target_iops=target,
                status=status,
                started_at=None,
                finished_at=None,
                error="mock failure" if status == MeasurementStatus.FAILED else "",
                metrics={M.IOPS: iops, M.LATENCY_AVG: latency, M.LATENCY_MAX: latency * 5},
            )
            run.measurements.append(measurement)
        suite.runs.append(run)
    return suite


def test_collect_series_splits_by_condition():
    series = collect_series(make_suite())
    assert [s.title for s in series] == ["read_pct=100", "read_pct=0"]
    assert len(series[0].points) == 4
    assert len(series[0].ok_points) == 2
    assert len(series[0].warn_points) == 1
    assert len(series[0].failed_points) == 1
    # カーブは実測 IOPS 昇順(失敗点は含まない)
    assert [p.iops for p in series[0].curve_points] == [10000.0, 20000.0, 25000.0]


def test_exclusions_are_a_view_over_immutable_data():
    """外れ値除外は「ビュー定義」として与える(生データは不変。Phase 3 の布石)。"""
    suite = make_suite()
    excluded_id = suite.runs[0].measurements[0].id
    series = collect_series(suite, exclusions=[excluded_id])
    assert excluded_id not in [p.measurement_id for p in series[0].points]
    # 元データは変わらない
    assert len(suite.runs[0].measurements) == 4


def test_figure_embeds_measurement_id_as_customdata():
    figure = build_figure(make_suite())
    customdata = figure.data[0].customdata
    assert customdata[0][0] == 101  # measurement_id
    assert figure.data[0].hovertemplate is not None


def test_failed_and_warn_points_have_their_own_traces():
    figure = build_figure(make_suite())
    names = {trace.name for trace in figure.data}
    assert "正常" in names
    assert "目標 IOPS 未達" in names
    assert "失敗" in names


def test_write_report_is_self_contained(tmp_path):
    path = write_report(make_suite(), tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    assert path.stat().st_size > 100_000          # plotly.js が同梱されている
    assert "<script" in html
    # 外部リソースを読みに行かない(オフライン閲覧要件)。
    # plotly.js のバンドル文字列に URL は含まれるが、script タグの src は無いこと
    assert not re.search(r'<script[^>]*\bsrc\s*=', html)
    assert not re.search(r'<link[^>]*\bhref\s*=\s*"http', html)


def test_empty_suite_raises():
    suite = SuiteData(id=1, name="empty", created_at="", description="", results_dir="")
    with pytest.raises(ReportError):
        build_figure(suite)


async def test_report_from_real_run(make_config_dir, tmp_path):
    """実行 → DB → レポートの一連が通ること。"""
    from benchmanager.config import load_config
    from benchmanager.engine import Engine, EngineOptions

    config = load_config(
        make_config_dir(
            """
            [test]
            tool = "mock"
            test_suite = "report-suite"

            [strategy]
            type = "range"
            start_iops = 10000
            end_iops = 30000
            step_iops = 10000

            [timing]
            duration_sec = 1
            warmup_sec = 0

            [matrix]
            read_pct = [100, 0]
            """
        )
    )
    engine = Engine(
        config, EngineOptions(results_root=tmp_path / "r", db_path=tmp_path / "db.sqlite")
    )
    await engine.run()
    with Store(tmp_path / "db.sqlite") as store:
        suite = store.load_suite("report-suite")
        path = write_report(suite, tmp_path / "out.html")
    assert path.is_file()
