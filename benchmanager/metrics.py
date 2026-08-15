"""正規化メトリクス名の定数(§9)。

ドライバは自身の出力をこの名前に正規化して返す。DB は縦持ちなので、
ここに名前を足すだけでスキーマ変更なしにメトリクスを増やせる。
"""

from __future__ import annotations

IOPS = "iops"
READ_IOPS = "read_iops"
WRITE_IOPS = "write_iops"
LATENCY_AVG = "latency_avg"
LATENCY_MAX = "latency_max"
LATENCY_STDDEV = "latency_stddev"
READ_LATENCY = "read_latency"
WRITE_LATENCY = "write_latency"
READ_LATENCY_MAX = "read_latency_max"
WRITE_LATENCY_MAX = "write_latency_max"
THROUGHPUT_MBPS = "throughput_mbps"
QUEUE_DEPTH = "queue_depth"
CPU_USED_PCT = "cpu_used_pct"
CPU_SYS_PCT = "cpu_sys_pct"
READ_PCT = "read_pct"
XFERSIZE_BYTES = "xfersize_bytes"
LATENCY_P50 = "latency_p50"
LATENCY_P90 = "latency_p90"
LATENCY_P95 = "latency_p95"
LATENCY_P99 = "latency_p99"

#: メトリクス名 -> 単位
UNITS: dict[str, str] = {
    IOPS: "iops",
    READ_IOPS: "iops",
    WRITE_IOPS: "iops",
    LATENCY_AVG: "ms",
    LATENCY_MAX: "ms",
    LATENCY_STDDEV: "ms",
    READ_LATENCY: "ms",
    WRITE_LATENCY: "ms",
    READ_LATENCY_MAX: "ms",
    WRITE_LATENCY_MAX: "ms",
    THROUGHPUT_MBPS: "MB/s",
    QUEUE_DEPTH: "",
    CPU_USED_PCT: "%",
    CPU_SYS_PCT: "%",
    READ_PCT: "%",
    XFERSIZE_BYTES: "bytes",
    LATENCY_P50: "ms",
    LATENCY_P90: "ms",
    LATENCY_P95: "ms",
    LATENCY_P99: "ms",
}

#: 測定点の判定に用いる主要メトリクス
PRIMARY_METRICS = (IOPS, LATENCY_AVG)


def unit_for(name: str) -> str:
    """メトリクス名に対応する単位(未知なら空文字)。"""
    return UNITS.get(name, "")
