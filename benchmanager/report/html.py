"""Plotly による IOPS-Latency カーブレポート(§11)。

* plotly.js を HTML 内に同梱した自己完結ファイル(オフライン閲覧可)
* テスト条件ごとにサブプロットをグリッド配置
* 各点に ``customdata`` として measurement_id を埋め込む(Phase 3 の外れ値除外 UI の布石)
* 失敗点・警告(目標 IOPS 未達)は視覚的に区別

Phase 1 では実装しないが構造として考慮している点:

* 外れ値の除外は「ビュー定義」(``exclusions``)として与える。生データ・DB は不変
* CSV/Excel/PNG/PowerPoint 出力は :func:`build_figure` が返す図と
  :func:`collect_series` が返す系列データを入力にすれば追加できる
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import metrics as M
from ..errors import ReportError
from ..i18n import t
from ..store.store import MeasurementRow, RunRow, SuiteData
from ..types import MeasurementStatus

#: サブプロットの最大列数
MAX_COLS = 3

COLOR_OK = "#1f77b4"
COLOR_WARN = "#ff7f0e"
COLOR_FAILED = "#d62728"


@dataclass
class SeriesPoint:
    """レポート 1 点。"""

    measurement_id: int
    target_iops: int | None
    iops: float
    latency_avg: float
    latency_max: float | None
    status: str
    error: str = ""


@dataclass
class Series:
    """1 テスト条件分の系列。"""

    title: str
    conditions: dict[str, Any]
    points: list[SeriesPoint] = field(default_factory=list)

    @property
    def ok_points(self) -> list[SeriesPoint]:
        return [p for p in self.points if p.status == MeasurementStatus.OK]

    @property
    def warn_points(self) -> list[SeriesPoint]:
        return [p for p in self.points if p.status == MeasurementStatus.WARN]

    @property
    def failed_points(self) -> list[SeriesPoint]:
        return [p for p in self.points if p.status == MeasurementStatus.FAILED]

    @property
    def curve_points(self) -> list[SeriesPoint]:
        """カーブとして結ぶ点(成功 + 警告)。実測 IOPS 昇順。"""
        points = [p for p in self.points if p.status != MeasurementStatus.FAILED]
        return sorted(points, key=lambda p: p.iops)


def varying_keys(runs: list[RunRow]) -> list[str]:
    """条件のうち run 間で値が異なるキー(サブプロット見出しに使う)。"""
    if not runs:
        return []
    keys = list(runs[0].conditions)
    varying = []
    for key in keys:
        values = {str(run.conditions.get(key)) for run in runs}
        if len(values) > 1:
            varying.append(key)
    return varying


def _title_for(run: RunRow, keys: list[str]) -> str:
    if not keys:
        return t("report.condition_title", conditions=run.conditions.get("read_pct", "-"))
    return ", ".join(f"{key}={run.conditions.get(key)}" for key in keys)


def collect_series(suite: SuiteData, exclusions: Iterable[int] = ()) -> list[Series]:
    """DB のスイートデータをレポート用の系列に変換する。

    :param exclusions: 除外する measurement_id(Phase 3 の「ビュー定義」相当)
    """
    excluded = set(exclusions)
    keys = varying_keys(suite.runs)
    series_list: list[Series] = []
    for run in suite.runs:
        series = Series(title=_title_for(run, keys), conditions=run.conditions)
        for measurement in run.measurements:
            if measurement.id in excluded:
                continue
            series.points.append(_to_point(measurement))
        series_list.append(series)
    return series_list


def _to_point(measurement: MeasurementRow) -> SeriesPoint:
    return SeriesPoint(
        measurement_id=measurement.id,
        target_iops=measurement.target_iops,
        iops=float(measurement.metric(M.IOPS) or 0.0),
        latency_avg=float(measurement.metric(M.LATENCY_AVG) or 0.0),
        latency_max=measurement.metric(M.LATENCY_MAX),
        status=measurement.status,
        error=measurement.error,
    )


def _hover_template() -> str:
    return (
        f"{t('report.hover_target')}: %{{customdata[1]}}<br>"
        f"{t('report.hover_iops')}: %{{x:,.1f}}<br>"
        f"{t('report.hover_latency')}: %{{y:.3f}} ms<br>"
        f"{t('report.hover_latency_max')}: %{{customdata[2]}}<br>"
        f"{t('report.hover_measurement')}: %{{customdata[0]}}"
        "<extra></extra>"
    )


def _customdata(points: list[SeriesPoint]) -> list[list[Any]]:
    return [
        [
            point.measurement_id,
            "max" if point.target_iops is None else f"{point.target_iops:,}",
            "-" if point.latency_max is None else f"{point.latency_max:.3f} ms",
        ]
        for point in points
    ]


def build_figure(suite: SuiteData, exclusions: Iterable[int] = ()):
    """Plotly の Figure を組み立てる(HTML 化とは分離しておく)。"""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    series_list = collect_series(suite, exclusions)
    series_list = [s for s in series_list if s.points]
    if not series_list:
        raise ReportError(t("report.no_data", suite=suite.name))

    cols = min(MAX_COLS, len(series_list))
    rows = math.ceil(len(series_list) / cols)
    figure = make_subplots(
        rows=rows,
        cols=cols,
        subplot_titles=[series.title for series in series_list],
        horizontal_spacing=0.08,
        vertical_spacing=max(0.08, 0.35 / rows),
    )

    hover = _hover_template()
    legend_shown = {"ok": False, "warn": False, "failed": False}

    for index, series in enumerate(series_list):
        row = index // cols + 1
        col = index % cols + 1

        curve = series.curve_points
        if curve:
            figure.add_trace(
                go.Scatter(
                    x=[p.iops for p in curve],
                    y=[p.latency_avg for p in curve],
                    customdata=_customdata(curve),
                    mode="lines+markers",
                    name=t("report.legend_ok"),
                    legendgroup="ok",
                    showlegend=not legend_shown["ok"],
                    line={"color": COLOR_OK, "width": 2},
                    marker={"color": COLOR_OK, "size": 8},
                    hovertemplate=hover,
                ),
                row=row,
                col=col,
            )
            legend_shown["ok"] = True

        warn = series.warn_points
        if warn:
            figure.add_trace(
                go.Scatter(
                    x=[p.iops for p in warn],
                    y=[p.latency_avg for p in warn],
                    customdata=_customdata(warn),
                    mode="markers",
                    name=t("report.legend_warn"),
                    legendgroup="warn",
                    showlegend=not legend_shown["warn"],
                    marker={
                        "color": COLOR_WARN,
                        "size": 12,
                        "symbol": "diamond-open",
                        "line": {"width": 2},
                    },
                    hovertemplate=hover,
                ),
                row=row,
                col=col,
            )
            legend_shown["warn"] = True

        failed = series.failed_points
        if failed:
            # 失敗点は実測値が無いため、目標 IOPS の位置に成功点の最大 latency で示す
            baseline = max((p.latency_avg for p in curve), default=1.0)
            figure.add_trace(
                go.Scatter(
                    x=[p.target_iops or 0 for p in failed],
                    y=[baseline for _ in failed],
                    customdata=_customdata(failed),
                    mode="markers",
                    name=t("report.legend_failed"),
                    legendgroup="failed",
                    showlegend=not legend_shown["failed"],
                    marker={"color": COLOR_FAILED, "size": 13, "symbol": "x-thin", "line": {"width": 3}},
                    hovertemplate=hover,
                ),
                row=row,
                col=col,
            )
            legend_shown["failed"] = True

        figure.update_xaxes(title_text=t("report.xaxis"), row=row, col=col, rangemode="tozero")
        figure.update_yaxes(title_text=t("report.yaxis"), row=row, col=col, rangemode="tozero")

    tool = suite.runs[0].tool if suite.runs else "-"
    figure.update_layout(
        title={
            "text": (
                f"{t('report.title', suite=suite.name)}<br>"
                f"<sub>{t('report.subtitle', tool=tool, points=suite.measurement_count(), created=suite.created_at)}</sub>"
            )
        },
        height=max(420, 380 * rows),
        hovermode="closest",
        template="plotly_white",
    )
    return figure


def write_report(suite: SuiteData, output_path: str | Path, exclusions: Iterable[int] = ()) -> Path:
    """自己完結 HTML を書き出す。"""
    figure = build_figure(suite, exclusions)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    html = figure.to_html(
        include_plotlyjs="inline",  # オフライン環境で開けるよう同梱する
        full_html=True,
        config={"displaylogo": False, "responsive": True},
    )
    path.write_text(html, encoding="utf-8")
    return path


def default_output_name(suite_name: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in suite_name)
    return f"{safe}_{stamp}.html"
