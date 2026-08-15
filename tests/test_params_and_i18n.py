"""params.toml(§7)と多言語対応(§8)。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from benchmanager.drivers.mock.driver import MockDriver
from benchmanager.drivers.registry import available_tools, get_driver_class
from benchmanager.drivers.vdbench.driver import VdbenchDriver
from benchmanager.i18n import set_lang, t


# --------------------------------------------------------------------------
# params.toml
# --------------------------------------------------------------------------


def test_vdbench_params_spec_loads():
    spec = VdbenchDriver.load_params_spec()
    assert spec.meta.tool == "vdbench"
    assert len(spec.params) >= 15  # Phase 1 では主要パラメータ 15 個程度
    assert {"read_pct", "block_size", "threads", "seekpct", "openflags"} <= set(spec.names())


def test_sweepable_flags():
    spec = VdbenchDriver.load_params_spec()
    sweepable = set(spec.sweepable_names())
    assert {"read_pct", "block_size", "threads"} <= sweepable
    assert "jvms" not in sweepable  # 掃引対象ではない


def test_dynamic_model_validates_bounds():
    spec = VdbenchDriver.load_params_spec()
    with pytest.raises(ValidationError):
        spec.validate_values({"read_pct": 101})
    with pytest.raises(ValidationError):
        spec.validate_values({"threads": 0})
    assert spec.validate_values({"read_pct": 70})["read_pct"] == 70


def test_dynamic_model_rejects_unknown_key():
    spec = VdbenchDriver.load_params_spec()
    with pytest.raises(ValidationError):
        spec.validate_values({"nosuchparam": 1})


def test_choice_type_is_restricted():
    spec = VdbenchDriver.load_params_spec()
    assert spec.validate_values({"openflags": "o_direct"})["openflags"] == "o_direct"
    with pytest.raises(ValidationError):
        spec.validate_values({"openflags": "nonsense"})


def test_size_type_pattern():
    spec = VdbenchDriver.load_params_spec()
    assert spec.validate_values({"block_size": "64k"})["block_size"] == "64k"
    with pytest.raises(ValidationError):
        spec.validate_values({"block_size": "sixty-four"})


def test_labels_are_localized():
    spec = VdbenchDriver.load_params_spec()
    param = spec.by_name()["read_pct"]
    set_lang("ja")
    assert param.label.get() == "Read比率(%)"
    set_lang("en")
    assert param.label.get() == "Read %"


def test_defaults_are_applied():
    spec = MockDriver.load_params_spec()
    defaults = spec.defaults()
    assert defaults["read_pct"] == 100
    assert defaults["block_size"] == "4k"


def test_registry_lists_tools():
    assert set(available_tools()) == {"vdbench", "mock"}
    assert get_driver_class("vdbench") is VdbenchDriver
    with pytest.raises(KeyError):
        get_driver_class("nope")


def test_params_spec_is_shipped_with_driver():
    assert VdbenchDriver.params_spec_path().is_file()
    assert MockDriver.params_spec_path().is_file()


# --------------------------------------------------------------------------
# i18n
# --------------------------------------------------------------------------


def test_japanese_messages():
    set_lang("ja")
    assert t("config.ok", path="x") == "設定は正常です: x"


def test_english_messages():
    set_lang("en")
    assert t("config.ok", path="x") == "Config is valid: x"


def test_unknown_key_returns_key():
    set_lang("ja")
    assert t("no.such.key") == "no.such.key"


def test_missing_placeholder_does_not_raise():
    set_lang("ja")
    # 引数を渡し忘れても例外にはせず、テンプレートをそのまま返す
    assert "{path}" in t("config.ok")


def test_all_ja_keys_exist_in_en():
    """文言の追加漏れを防ぐ(英語がフォールバック先のため)。"""
    from benchmanager.i18n import _load

    ja = set(_load("ja"))
    en = set(_load("en"))
    assert ja - en == set()
    assert en - ja == set()
