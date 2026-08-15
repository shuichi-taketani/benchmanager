"""vdbench 出力のパーサー。

対象:

* ``flatfile.html`` — 1 行 1 インターバルの列指向テキスト。集計行(``avg_31-215`` など)を
  測定点の代表値、それ以外の行を時系列として扱う
* ``histogram.html`` — 応答時間ヒストグラム。累積 % から p50/p90/p95/p99 を線形補間で推定

実行環境に依存しないよう、入力は「ファイルの中身(文字列)」のみとする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import metrics as M
from ...types import Metric, TimeseriesPoint

SOURCE = "vdbench"

FLATFILE_NAME = "flatfile.html"
HISTOGRAM_NAME = "histogram.html"
SUMMARY_NAME = "summary.html"

#: flatfile のヘッダ行を見つけるための先頭カラム
_HEADER_FIRST_COLUMN = "tod"

_TAG_RE = re.compile(r"<[^>]+>")
_TOD_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$")
_HIST_LINE_RE = re.compile(
    r"^\s*([\d.]+)\s*<\s*([\d.]+|max)\s+([\d,]+)\s+([\d.]+)\s+([\d.]+)"
)

#: flatfile の列名 -> 正規化メトリクス名(そのまま値を使えるもの)
_DIRECT_COLUMNS: dict[str, str] = {
    "rate": M.IOPS,
    "resp": M.LATENCY_AVG,
    "resp_max": M.LATENCY_MAX,
    "resp_std": M.LATENCY_STDDEV,
    "read_resp": M.READ_LATENCY,
    "write_resp": M.WRITE_LATENCY,
    "read_max": M.READ_LATENCY_MAX,
    "write_max": M.WRITE_LATENCY_MAX,
    "MB/sec": M.THROUGHPUT_MBPS,
    "queue_depth": M.QUEUE_DEPTH,
    "cpu_used": M.CPU_USED_PCT,
    "cpu_kernel": M.CPU_SYS_PCT,
    "read%": M.READ_PCT,
    "bytes/io": M.XFERSIZE_BYTES,
}


class VdbenchParseError(ValueError):
    """flatfile の形式が想定と異なる場合。"""


@dataclass
class FlatfileRow:
    """flatfile の 1 行。"""

    values: dict[str, Any] = field(default_factory=dict)

    @property
    def interval(self) -> str:
        return str(self.values.get("Interval", ""))

    @property
    def is_summary(self) -> bool:
        """``avg_31-215`` のような集計行か。"""
        return self.interval.lower().startswith("avg")

    @property
    def tod(self) -> str:
        return str(self.values.get("tod", ""))

    def number(self, column: str) -> float | None:
        return _to_float(self.values.get(column))


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in ("n/a", "na", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _tod_seconds(tod: str) -> float | None:
    match = _TOD_RE.match(tod.strip())
    if not match:
        return None
    hour, minute, second, frac = match.groups()
    total = int(hour) * 3600 + int(minute) * 60 + int(second)
    if frac:
        total += float(f"0.{frac}")
    return float(total)


def parse_flatfile(text: str) -> list[FlatfileRow]:
    """flatfile.html を行のリストに変換する。"""
    columns: list[str] | None = None
    rows: list[FlatfileRow] = []
    for raw_line in text.splitlines():
        line = _strip_html(raw_line).rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("*"):
            continue  # 先頭のコメント(列の説明)
        fields = line.split()
        if columns is None:
            if fields[0] == _HEADER_FIRST_COLUMN:
                columns = fields
            continue
        if fields[0] == _HEADER_FIRST_COLUMN:
            continue  # ヘッダの繰り返し
        if len(fields) < len(columns):
            # 末尾列が欠けている行は None 埋めで受ける
            fields = fields + [""] * (len(columns) - len(fields))
        rows.append(FlatfileRow(values=dict(zip(columns, fields))))
    if columns is None:
        raise VdbenchParseError("flatfile header line (starting with 'tod') not found")
    return rows


def summary_row(rows: list[FlatfileRow]) -> FlatfileRow | None:
    """集計行(``avg_*``)。複数ある場合は最後のものを返す。"""
    summaries = [row for row in rows if row.is_summary]
    return summaries[-1] if summaries else None


def interval_rows(rows: list[FlatfileRow]) -> list[FlatfileRow]:
    """時系列として使えるインターバル行。"""
    return [row for row in rows if not row.is_summary and row.interval.isdigit()]


def row_to_metrics(row: FlatfileRow, source: str = SOURCE) -> list[Metric]:
    """flatfile の 1 行を正規化メトリクスへ変換する。"""
    metrics: list[Metric] = []
    for column, name in _DIRECT_COLUMNS.items():
        value = row.number(column)
        if value is not None:
            metrics.append(Metric.make(name, value, source))

    iops = row.number("rate")
    read_pct = row.number("read%")
    if iops is not None and read_pct is not None:
        ratio = read_pct / 100.0
        metrics.append(Metric.make(M.READ_IOPS, iops * ratio, source))
        metrics.append(Metric.make(M.WRITE_IOPS, iops * (1.0 - ratio), source))
    return metrics


def parse_flatfile_metrics(text: str) -> list[Metric]:
    """flatfile の集計行から測定点の代表メトリクスを得る。"""
    rows = parse_flatfile(text)
    summary = summary_row(rows)
    if summary is None:
        raise VdbenchParseError("no summary (avg_*) row in flatfile")
    return row_to_metrics(summary)


def parse_flatfile_timeseries(text: str) -> list[TimeseriesPoint]:
    """flatfile のインターバル行から時系列メトリクスを得る。

    ``ts`` は最初のインターバル行の時刻を 0 とした経過秒。
    """
    rows = interval_rows(parse_flatfile(text))
    points: list[TimeseriesPoint] = []
    origin: float | None = None
    for row in rows:
        seconds = _tod_seconds(row.tod)
        if seconds is None:
            ts = float(int(row.interval))
        else:
            if origin is None:
                origin = seconds
            ts = seconds - origin
            if ts < 0:  # 日付跨ぎ
                ts += 24 * 3600
        for column, name in _DIRECT_COLUMNS.items():
            if name not in (M.IOPS, M.LATENCY_AVG, M.LATENCY_MAX, M.THROUGHPUT_MBPS, M.QUEUE_DEPTH):
                continue
            value = row.number(column)
            if value is not None:
                points.append(TimeseriesPoint(name=name, ts=ts, value=value, source=SOURCE))
    return points


# --------------------------------------------------------------------------
# ヒストグラム(パーセンタイル)
# --------------------------------------------------------------------------


@dataclass
class HistogramBucket:
    low_ms: float
    high_ms: float | None  # None = 上限なし(``< max``)
    count: float
    pct: float
    cum_pct: float


def parse_histogram(text: str) -> list[HistogramBucket]:
    """histogram.html からバケットを取り出す。

    read / write / total の表が複数ある場合は最後の表(通常は合計)を採用する。
    """
    tables: list[list[HistogramBucket]] = []
    current: list[HistogramBucket] = []
    for raw_line in text.splitlines():
        line = _strip_html(raw_line)
        match = _HIST_LINE_RE.match(line)
        if not match:
            if current:
                tables.append(current)
                current = []
            continue
        low, high, count, pct, cum = match.groups()
        current.append(
            HistogramBucket(
                low_ms=float(low),
                high_ms=None if high == "max" else float(high),
                count=float(count.replace(",", "")),
                pct=float(pct),
                cum_pct=float(cum),
            )
        )
    if current:
        tables.append(current)
    return tables[-1] if tables else []


def percentile_from_histogram(buckets: list[HistogramBucket], percentile: float) -> float | None:
    """累積 % から指定パーセンタイルの応答時間(ms)を線形補間で推定する。"""
    if not buckets:
        return None
    previous_cum = 0.0
    for bucket in buckets:
        if bucket.cum_pct >= percentile and bucket.count > 0:
            high = bucket.high_ms if bucket.high_ms is not None else bucket.low_ms
            span = bucket.cum_pct - previous_cum
            if span <= 0:
                return bucket.low_ms
            ratio = (percentile - previous_cum) / span
            return bucket.low_ms + (high - bucket.low_ms) * max(0.0, min(1.0, ratio))
        previous_cum = max(previous_cum, bucket.cum_pct)
    return None


def parse_histogram_metrics(text: str, source: str = SOURCE) -> list[Metric]:
    """p50 / p90 / p95 / p99 をメトリクスとして返す(§4.4 の「保存は行う」)。"""
    buckets = parse_histogram(text)
    wanted = {M.LATENCY_P50: 50.0, M.LATENCY_P90: 90.0, M.LATENCY_P95: 95.0, M.LATENCY_P99: 99.0}
    metrics: list[Metric] = []
    for name, percentile in wanted.items():
        value = percentile_from_histogram(buckets, percentile)
        if value is not None:
            metrics.append(Metric.make(name, value, source))
    return metrics


# --------------------------------------------------------------------------
# ディレクトリ単位
# --------------------------------------------------------------------------


def find_output_file(directory: Path, filename: str) -> Path | None:
    """測定ディレクトリ配下から vdbench の出力ファイルを探す。"""
    directory = Path(directory)
    direct = directory / filename
    if direct.is_file():
        return direct
    matches = sorted(directory.rglob(filename))
    return matches[0] if matches else None
