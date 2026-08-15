"""ツール名・パッケージ名などの一元管理(§1: 名称は一箇所で管理)。"""

from __future__ import annotations

#: Python パッケージ名
PACKAGE_NAME = "benchmanager"

#: CLI コマンド名
CLI_NAME = "benchman"

#: 表示用の製品名
PRODUCT_NAME = "Benchmark Manager"

#: バージョン
VERSION = "0.1.0"

#: 既定の設定ディレクトリ名(カレント配下)
DEFAULT_CONFIG_DIRNAME = "config"

#: 設定ディレクトリ内のファイル名
TEST_CONFIG_FILENAME = "test.toml"
SERVERS_CONFIG_FILENAME = "servers.toml"
STORAGE_CONFIG_FILENAME = "storage.toml"

#: 結果ディレクトリの既定の親
DEFAULT_RESULTS_DIRNAME = "results"

#: 既定の SQLite ファイル名
DEFAULT_DB_FILENAME = "benchman.sqlite"

#: 結果ディレクトリ内のファイル/ディレクトリ名
MANIFEST_FILENAME = "manifest.toml"
RAW_DIRNAME = "raw"
ENV_RAW_DIRNAME = "env"
CONFIG_COPY_DIRNAME = "config"
RUN_META_FILENAME = "run.toml"
MEASUREMENT_META_FILENAME = "measurement.toml"
PARAMS_COPY_FILENAME = "params.toml"
