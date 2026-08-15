"""SQLite スキーマ(§5.1)。

メトリクスは縦持ち。ツール追加やサーバ台数の増減でスキーマが変わらないようにする。
SQLite は生データから何度でも再生成できる二次的な存在という位置づけ(§5.2)。
"""

from __future__ import annotations

SCHEMA_VERSION = 1

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_info (
    version     INTEGER NOT NULL
);

-- 一連のテスト群
CREATE TABLE IF NOT EXISTS test_suite (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    results_dir TEXT    NOT NULL DEFAULT '',
    UNIQUE (name, results_dir)
);

-- 1 条件(マトリクスの 1 点)のテスト
CREATE TABLE IF NOT EXISTS test_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    test_suite_id   INTEGER NOT NULL REFERENCES test_suite(id) ON DELETE CASCADE,
    tool            TEXT    NOT NULL,
    conditions_json TEXT    NOT NULL DEFAULT '{}',
    status          TEXT    NOT NULL DEFAULT 'ok',
    started_at      TEXT,
    finished_at     TEXT,
    raw_dir         TEXT    NOT NULL DEFAULT '',
    error           TEXT    NOT NULL DEFAULT '',
    seq             INTEGER NOT NULL DEFAULT 0
);

-- 1 測定点
CREATE TABLE IF NOT EXISTS measurement (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    test_run_id  INTEGER NOT NULL REFERENCES test_run(id) ON DELETE CASCADE,
    target_iops  INTEGER,            -- NULL = 最大負荷計測 (iorate=max)
    seq          INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'ok',
    started_at   TEXT,
    finished_at  TEXT,
    raw_dir      TEXT    NOT NULL DEFAULT '',
    error        TEXT    NOT NULL DEFAULT ''
);

-- メトリクスは縦持ち
CREATE TABLE IF NOT EXISTS metric (
    measurement_id INTEGER NOT NULL REFERENCES measurement(id) ON DELETE CASCADE,
    source         TEXT    NOT NULL DEFAULT '',   -- 'vdbench' | 'sysstat:server1' 等
    name           TEXT    NOT NULL,              -- 'iops' | 'latency_avg' 等
    value          REAL,
    unit           TEXT    NOT NULL DEFAULT ''
);

-- 時系列(Phase 1 では vdbench の interval 出力)
CREATE TABLE IF NOT EXISTS timeseries_metric (
    measurement_id INTEGER NOT NULL REFERENCES measurement(id) ON DELETE CASCADE,
    source         TEXT    NOT NULL DEFAULT '',
    name           TEXT    NOT NULL,
    ts             REAL    NOT NULL,              -- 測定開始からの経過秒
    value          REAL
);

CREATE INDEX IF NOT EXISTS idx_test_run_suite   ON test_run (test_suite_id);
CREATE INDEX IF NOT EXISTS idx_measurement_run  ON measurement (test_run_id);
CREATE INDEX IF NOT EXISTS idx_metric_meas      ON metric (measurement_id);
CREATE INDEX IF NOT EXISTS idx_metric_name      ON metric (name);
CREATE INDEX IF NOT EXISTS idx_ts_meas          ON timeseries_metric (measurement_id, name);
"""
