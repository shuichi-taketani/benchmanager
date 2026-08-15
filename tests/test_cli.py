"""CLI(§12)。typer の CliRunner で実行する(実機・ネットワーク不要)。"""

from __future__ import annotations

import os

from typer.testing import CliRunner

from benchmanager.cli import app

runner = CliRunner()

RANGE_TOML = """
[test]
tool = "mock"
test_suite = "cli-suite"

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


def test_config_validate_ok(config_dir):
    result = runner.invoke(app, ["config", "validate", str(config_dir)])
    assert result.exit_code == 0
    assert "設定は正常です" in result.stdout
    assert "test.toml, servers.toml, storage.toml" in result.stdout


def test_config_validate_error_exit_code(make_config_dir):
    directory = make_config_dir('[test]\ntool = "mock"\n[strategy]\ntype = "range"\n')
    result = runner.invoke(app, ["config", "validate", str(directory)])
    assert result.exit_code == 1
    assert "エラー" in result.output
    assert "test.toml" in result.output


def test_plan_lists_conditions(make_config_dir):
    directory = make_config_dir(RANGE_TOML)
    result = runner.invoke(app, ["plan", str(directory)])
    assert result.exit_code == 0
    assert "テスト条件の展開結果(2 件)" in result.stdout
    assert "read_pct=100" in result.stdout
    assert "推定所要時間" in result.stdout


def test_params_lists_definitions():
    result = runner.invoke(app, ["params", "vdbench"])
    assert result.exit_code == 0
    assert "read_pct" in result.stdout
    assert "block_size" in result.stdout
    assert "tool_params.raw" in result.stdout


def test_params_unknown_tool():
    result = runner.invoke(app, ["params", "nosuch"])
    assert result.exit_code == 1
    assert "nosuch" in result.output


def test_run_import_report_roundtrip(make_config_dir, tmp_path, monkeypatch):
    directory = make_config_dir(RANGE_TOML)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            str(directory),
            "--results-dir",
            "results",
            "--db",
            "db.sqlite",
            "--report",
            "out.html",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out.html").is_file()

    results_dir = next((tmp_path / "results").iterdir())

    # import で別 DB を再構築できる
    result = runner.invoke(app, ["import", str(results_dir), "--db", "db2.sqlite"])
    assert result.exit_code == 0
    assert (tmp_path / "db2.sqlite").is_file()

    # report が再構築 DB からも生成できる
    result = runner.invoke(
        app, ["report", "cli-suite", "--db", "db2.sqlite", "-o", "out2.html"]
    )
    assert result.exit_code == 0
    assert (tmp_path / "out2.html").is_file()
    assert (tmp_path / "out.html").stat().st_size == (tmp_path / "out2.html").stat().st_size


def test_report_unknown_suite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["report", "nosuch", "--db", "empty.sqlite"])
    assert result.exit_code == 1
    assert "nosuch" in result.output


def test_lang_option_switches_messages(config_dir):
    result = runner.invoke(app, ["--lang", "en", "config", "validate", str(config_dir)])
    assert result.exit_code == 0
    assert "Config is valid" in result.stdout


def test_examples_are_valid():
    """同梱のサンプル設定が検証を通ること。"""
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples")
    for name in ("mock", "mock-bisect", "vdbench"):
        result = runner.invoke(app, ["config", "validate", os.path.join(root, name)])
        assert result.exit_code == 0, f"{name}: {result.output}"
