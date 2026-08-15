"""共通フィクスチャ。

すべてのテストはネットワーク・実機なしで動くこと(§10)。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
VDBENCH_OUTPUT = FIXTURES / "vdbench_output"

MOCK_SERVERS_TOML = """
[[servers]]
name = "bench1"
host = "bench1.example.local"
user = "bench"
"""

MOCK_STORAGE_TOML = """
[storage]
name = "test-storage"
luns = ["/mnt/bench/file1"]
"""

MOCK_TEST_TOML = """
[test]
tool = "mock"
test_suite = "unit-test-suite"

[strategy]
type = "range"
start_iops = 10000
end_iops = 30000
step_iops = 10000

[timing]
duration_sec = 10
warmup_sec = 1

[tool_params]
max_iops = 100000
base_latency_ms = 0.4
k = 50000.0

[matrix]
read_pct = [100, 0]
"""


def write_config_dir(directory: Path, test_toml: str, servers_toml: str = MOCK_SERVERS_TOML,
                     storage_toml: str = MOCK_STORAGE_TOML) -> Path:
    """3 ファイルからなる設定ディレクトリを作る。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test.toml").write_text(textwrap.dedent(test_toml), encoding="utf-8")
    (directory / "servers.toml").write_text(textwrap.dedent(servers_toml), encoding="utf-8")
    (directory / "storage.toml").write_text(textwrap.dedent(storage_toml), encoding="utf-8")
    return directory


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """モックドライバ + range 戦略の設定ディレクトリ。"""
    return write_config_dir(tmp_path / "config", MOCK_TEST_TOML)


@pytest.fixture
def make_config_dir(tmp_path: Path):
    """任意の test.toml から設定ディレクトリを作るファクトリ。"""

    def factory(test_toml: str, name: str = "config", **kwargs) -> Path:
        return write_config_dir(tmp_path / name, test_toml, **kwargs)

    return factory


@pytest.fixture
def flatfile_text() -> str:
    return (VDBENCH_OUTPUT / "flatfile.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def histogram_text() -> str:
    return (VDBENCH_OUTPUT / "histogram.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(autouse=True)
def japanese_locale():
    """テスト中のロケールを固定する。"""
    from benchmanager.i18n import set_lang

    set_lang("ja")
    yield
    set_lang(None)
