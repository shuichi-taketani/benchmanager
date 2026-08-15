"""設定の読み込みと検証(§6.4、受け入れ基準 5)。"""

from __future__ import annotations

import pytest

from benchmanager.config import load_config
from benchmanager.errors import ConfigError

from .conftest import MOCK_TEST_TOML


def test_load_valid_config(config_dir):
    config = load_config(config_dir)
    assert config.tool == "mock"
    assert config.suite_name == "unit-test-suite"
    assert config.test.strategy.type == "range"
    # params.toml の既定値で補完される
    assert config.tool_params["read_pct"] == 100
    assert config.tool_params["threads"] == 32


def test_missing_file_reports_filename(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "test.toml").write_text("[test]\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "config")
    assert "servers.toml" in str(exc.value)


def test_unknown_key_reports_file_key_and_line(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"
        typo_key = 1

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    message = str(exc.value)
    assert "test.toml" in message           # どのファイルか
    assert "test.typo_key" in message       # どのキーか
    assert "5 行目" in message               # 何行目か
    assert "定義されていない" in message      # 原因(日本語)


def test_missing_required_key(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "test.test_suite" in str(exc.value)
    assert "必須キー" in str(exc.value)


def test_servers_file_error_names_that_file(make_config_dir):
    directory = make_config_dir(MOCK_TEST_TOML, servers_toml="[[servers]]\nname = 'a'\n")
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    message = str(exc.value)
    assert "servers.toml" in message
    assert "servers.0.host" in message


def test_toml_syntax_error(make_config_dir):
    directory = make_config_dir("[test\ntool = 'mock'\n")
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "test.toml" in str(exc.value)
    assert "構文エラー" in str(exc.value)


def test_unknown_tool(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "nosuchtool"
        test_suite = "s"

        [strategy]
        type = "range"
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "nosuchtool" in str(exc.value)
    assert "vdbench" in str(exc.value)


def test_strategy_not_supported_by_driver(make_config_dir, monkeypatch):
    from benchmanager.drivers.mock.driver import MockDriver

    monkeypatch.setattr(MockDriver, "tool_strategies", classmethod(lambda cls: []))
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "vdbench_bisect"
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "vdbench_bisect" in str(exc.value)
    assert "range" in str(exc.value)


def test_range_requires_three_values(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 100
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "strategy.end_iops" in str(exc.value)


def test_range_step_direction_mismatch(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 100
        end_iops = 10
        step_iops = 10
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "step_iops" in str(exc.value)


def test_manual_max_load_requires_value(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "vdbench_bisect"

        [strategy.max_load]
        mode = "manual"
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "manual_iops" in str(exc.value)


def test_matrix_rejects_non_sweepable_param(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [matrix]
        max_iops = [1000, 2000]
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "max_iops" in str(exc.value)
    assert "read_pct" in str(exc.value)  # 掃引可能なパラメータを案内する


def test_tool_param_unknown_points_to_raw(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [tool_params]
        nosuchparam = 1
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "tool_params.raw" in str(exc.value)


def test_tool_param_out_of_range(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [tool_params]
        read_pct = 150
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "read_pct" in str(exc.value)


def test_raw_tool_params_pass_through(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [tool_params.raw]
        anything_goes = "xfersize_dist=(4k,50,8k,50)"
        """
    )
    config = load_config(directory)
    assert config.test.raw_tool_params == {"anything_goes": "xfersize_dist=(4k,50,8k,50)"}


def test_env_param_value_without_apply_definition(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [env_params.mount_opts]
        values = ["default", "nconnect"]

        [env_params.mount_opts.apply.default]
        commands = ["true"]
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "nconnect" in str(exc.value)


def test_env_param_storage_target_requires_management(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [env_params.pool]
        target = "storage"
        values = ["a"]

        [env_params.pool.apply.a]
        commands = ["true"]
        """
    )
    with pytest.raises(ConfigError) as exc:
        load_config(directory)
    assert "storage.toml" in str(exc.value)
    assert "management" in str(exc.value)


def test_env_params_ordering(make_config_dir):
    directory = make_config_dir(
        """
        [test]
        tool = "mock"
        test_suite = "s"

        [strategy]
        type = "range"
        start_iops = 1
        end_iops = 2
        step_iops = 1

        [env_params.inner]
        order = 5
        values = ["a"]
        [env_params.inner.apply.a]
        commands = ["true"]

        [env_params.outer]
        order = 1
        values = ["b"]
        [env_params.outer.apply.b]
        commands = ["true"]

        [env_params.unordered]
        values = ["c"]
        [env_params.unordered.apply.c]
        commands = ["true"]
        """
    )
    config = load_config(directory)
    assert [name for name, _ in config.env_params_in_order()] == ["outer", "inner", "unordered"]


def test_individual_file_override(tmp_path, config_dir):
    other = tmp_path / "other-storage.toml"
    other.write_text('[storage]\nname = "other"\nluns = ["/dev/sdz"]\n', encoding="utf-8")
    config = load_config(config_dir, storage_path=other)
    assert config.storage.storage.name == "other"
