"""設定ディレクトリの読み込みと検証(§6)。

``load_config()`` が唯一の入口。拡張子で分岐しているため、将来 YAML を併用する場合も
この関数の内部だけを拡張すればよい。

エラーメッセージは「**どのファイル**の何行目・どのキーが誤りか」を必ず含める。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import const
from ..errors import ConfigError
from ..i18n import t
from ..params import ParamsSpec
from .models import (
    EnvParamDef,
    ServersConfig,
    StorageConfig,
    TestConfig,
)

_HEADER_RE = re.compile(r"^\s*(\[\[|\[)\s*([^\]]+?)\s*(\]\]|\])\s*(?:#.*)?$")
_KEY_RE = re.compile(r"""^\s*((?:[A-Za-z0-9_\-]+|"[^"]*"|'[^']*')(?:\s*\.\s*(?:[A-Za-z0-9_\-]+|"[^"]*"|'[^']*'))*)\s*=""")


@dataclass
class ConfigPaths:
    """読み込んだ設定ファイルのパス。エラーメッセージとコピー保存に使う。"""

    config_dir: Path | None
    test: Path
    servers: Path
    storage: Path

    def all(self) -> list[Path]:
        return [self.test, self.servers, self.storage]


@dataclass
class Config:
    """3 ファイルを結合した設定一式。"""

    test: TestConfig
    servers: ServersConfig
    storage: StorageConfig
    paths: ConfigPaths
    params_spec: ParamsSpec
    #: params.toml で検証・補完済みのツールパラメータ(matrix 展開の既定値になる)
    tool_params: dict[str, Any] = field(default_factory=dict)

    @property
    def tool(self) -> str:
        return self.test.test.tool

    @property
    def suite_name(self) -> str:
        return self.test.test.test_suite

    def server_by_name(self, name: str):
        for server in self.servers.servers:
            if server.name == name:
                return server
        return None

    def primary_server(self):
        return self.servers.servers[0] if self.servers.servers else None

    def env_params_in_order(self) -> list[tuple[str, EnvParamDef]]:
        """``order`` 昇順(未指定は定義順の後ろ)に並べた環境パラメータ。

        小さいほど外側ループ = 切替コストが大きいもの、という規約(§6.1)。
        """
        items = list(self.test.env_params.items())

        def sort_key(entry: tuple[int, tuple[str, EnvParamDef]]) -> tuple[int, int]:
            index, (_, definition) = entry
            order = definition.order if definition.order is not None else len(items) + index
            return (order, index)

        return [item for _, item in sorted(enumerate(items), key=sort_key)]


# --------------------------------------------------------------------------
# 読み込み
# --------------------------------------------------------------------------


def _read_mapping(path: Path, label: str) -> tuple[dict[str, Any], str]:
    """設定ファイルを dict として読む(拡張子で分岐)。"""
    if not path.is_file():
        raise ConfigError(
            t("config.file_not_found", path=path, dir=path.parent, name=path.name)
        )
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".toml":
        try:
            return tomllib.loads(text), text
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(t("config.toml_syntax_error", file=label, message=exc)) from exc
    raise ConfigError(t("config.toml_syntax_error", file=label, message=f"unsupported extension: {suffix}"))


def find_key_line(text: str, loc: tuple[Any, ...]) -> int | None:
    """TOML テキスト内でキーが書かれている行番号を推定する。

    完全な TOML パーサではなく、エラーメッセージに行番号を添えるための補助。
    見つからない場合は ``None``。
    """
    positions: dict[tuple[Any, ...], int] = {}
    path: tuple[Any, ...] = ()
    array_counts: dict[str, int] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        header = _HEADER_RE.match(line)
        if header:
            is_array = header.group(1) == "[["
            name = header.group(2)
            parts = tuple(p.strip().strip('"').strip("'") for p in name.split("."))
            if is_array:
                index = array_counts.get(name, 0)
                array_counts[name] = index + 1
                path = parts + (index,)
            else:
                path = parts
            positions.setdefault(path, lineno)
            continue
        key = _KEY_RE.match(line)
        if key:
            parts = tuple(p.strip().strip('"').strip("'") for p in key.group(1).split("."))
            positions.setdefault(path + parts, lineno)
    if loc in positions:
        return positions[loc]
    # 末尾から段階的に短くして探す(dict 値の内部など)
    for length in range(len(loc) - 1, 0, -1):
        prefix = loc[:length]
        if prefix in positions:
            return positions[prefix]
    return None


def _format_validation_error(exc: ValidationError, label: str, text: str) -> str:
    lines = [t("config.validation_header", file=label, count=exc.error_count())]
    for error in exc.errors():
        loc = tuple(error["loc"])
        key = ".".join(str(part) for part in loc) or "(root)"
        lineno = find_key_line(text, loc)
        line_info = t("config.line_info", file=label, line=lineno) if lineno else ""
        if error["type"] == "extra_forbidden":
            message = t("config.extra_key")
        elif error["type"] == "missing":
            message = t("config.missing_key")
        else:
            message = error["msg"]
        if "input" in error and error["type"] != "missing":
            lines.append(
                t(
                    "config.validation_item",
                    key=key,
                    line_info=line_info,
                    message=message,
                    value=error["input"],
                )
            )
        else:
            lines.append(
                t("config.validation_item_no_value", key=key, line_info=line_info, message=message)
            )
    return "\n".join(lines)


def _parse_model(model: type[BaseModel], data: dict[str, Any], label: str, text: str):
    try:
        return model(**data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, label, text)) from exc


def load_config(
    config_dir: str | Path | None = None,
    *,
    test_path: str | Path | None = None,
    servers_path: str | Path | None = None,
    storage_path: str | Path | None = None,
    validate: bool = True,
) -> Config:
    """設定ディレクトリ(または個別指定されたファイル)を読み込んで結合する。

    :param config_dir: ``test.toml`` などを含むディレクトリ。既定は ``./config``
    :param validate: 意味的な検証(ドライバ整合など)まで行うか
    """
    directory = Path(config_dir) if config_dir is not None else Path(const.DEFAULT_CONFIG_DIRNAME)
    if any(p is None for p in (test_path, servers_path, storage_path)) and not directory.is_dir():
        raise ConfigError(t("config.dir_not_found", path=directory))

    paths = ConfigPaths(
        config_dir=directory if directory.is_dir() else None,
        test=Path(test_path) if test_path else directory / const.TEST_CONFIG_FILENAME,
        servers=Path(servers_path) if servers_path else directory / const.SERVERS_CONFIG_FILENAME,
        storage=Path(storage_path) if storage_path else directory / const.STORAGE_CONFIG_FILENAME,
    )

    test_data, test_text = _read_mapping(paths.test, paths.test.name)
    servers_data, servers_text = _read_mapping(paths.servers, paths.servers.name)
    storage_data, storage_text = _read_mapping(paths.storage, paths.storage.name)

    test_cfg = _parse_model(TestConfig, test_data, paths.test.name, test_text)
    servers_cfg = _parse_model(ServersConfig, servers_data, paths.servers.name, servers_text)
    storage_cfg = _parse_model(StorageConfig, storage_data, paths.storage.name, storage_text)

    params_spec = _load_params_spec(test_cfg.test.tool, paths.test.name)

    config = Config(
        test=test_cfg,
        servers=servers_cfg,
        storage=storage_cfg,
        paths=paths,
        params_spec=params_spec,
    )
    config.tool_params = _validate_tool_params(config, paths.test.name, test_text)
    if validate:
        validate_config(config)
    return config


def _load_params_spec(tool: str, label: str) -> ParamsSpec:
    from ..drivers.registry import available_tools, get_driver_class

    try:
        driver_cls = get_driver_class(tool)
    except KeyError as exc:
        raise ConfigError(
            t("config.unknown_tool", file=label, tool=tool, available=", ".join(available_tools()))
        ) from exc
    return driver_cls.load_params_spec()


def _validate_tool_params(config: Config, label: str, text: str) -> dict[str, Any]:
    """``[tool_params]`` を params.toml 定義に照らして検証する(§7)。"""
    spec = config.params_spec
    defined = config.test.defined_tool_params
    known = set(spec.names())
    unknown = [key for key in defined if key not in known]
    if unknown:
        raise ConfigError(
            t(
                "config.tool_param_unknown",
                file=label,
                key=unknown[0],
                tool=spec.meta.tool,
                available=", ".join(sorted(known)),
            )
        )
    values = dict(spec.defaults())
    values.update(defined)
    try:
        validated = spec.validate_values(values)
    except ValidationError as exc:
        error = exc.errors()[0]
        key = ".".join(str(part) for part in error["loc"])
        raise ConfigError(
            t("config.tool_param_invalid", file=label, key=key, message=error["msg"])
        ) from exc
    return validated


# --------------------------------------------------------------------------
# 意味的な検証
# --------------------------------------------------------------------------


def validate_config(config: Config) -> None:
    """結合後の設定を検証する(§6.4)。"""
    label = config.paths.test.name
    _validate_strategy(config, label)
    _validate_matrix(config, label)
    _validate_env_params(config, label)
    _validate_servers(config)


def _validate_strategy(config: Config, label: str) -> None:
    from ..drivers.registry import get_driver_class

    driver_cls = get_driver_class(config.tool)
    strategies = driver_cls.available_strategies()
    strategy = config.test.strategy
    if strategy.type not in strategies:
        raise ConfigError(
            t(
                "config.strategy_not_supported",
                file=label,
                strategy=strategy.type,
                tool=config.tool,
                available=", ".join(strategies),
            )
        )
    if strategy.type == "range":
        for key in ("start_iops", "end_iops", "step_iops"):
            if getattr(strategy, key) is None:
                raise ConfigError(t("config.range_param_missing", file=label, key=f"strategy.{key}"))
        if strategy.step_iops == 0:
            raise ConfigError(t("config.range_step_zero", file=label))
        direction = 1 if strategy.end_iops >= strategy.start_iops else -1
        if direction * strategy.step_iops < 0:
            raise ConfigError(
                t(
                    "config.range_step_direction",
                    file=label,
                    start=strategy.start_iops,
                    end=strategy.end_iops,
                )
            )
    else:
        if strategy.max_load.mode == "manual" and strategy.max_load.manual_iops is None:
            raise ConfigError(t("config.max_load_manual_missing", file=label))


def _validate_matrix(config: Config, label: str) -> None:
    sweepable = config.params_spec.sweepable_names()
    env_names = list(config.test.env_params)
    for key, values in config.test.matrix.items():
        if key not in sweepable and key not in env_names:
            raise ConfigError(
                t(
                    "config.matrix_not_sweepable",
                    file=label,
                    key=key,
                    sweepable=", ".join(sweepable) or "-",
                    env_params=", ".join(env_names) or "-",
                )
            )
        if not values:
            raise ConfigError(t("config.matrix_empty", file=label, key=key))
        if key in env_names:
            allowed = config.test.env_params[key].values
            for value in values:
                if value not in allowed:
                    raise ConfigError(
                        t("config.env_matrix_value_unknown", file=label, name=key, value=value)
                    )


def _validate_env_params(config: Config, label: str) -> None:
    for name, definition in config.test.env_params.items():
        for value in definition.values:
            apply = definition.apply.get(value)
            if apply is None or (not apply.commands and not apply.script):
                raise ConfigError(t("config.env_value_not_defined", file=label, name=name, value=value))
        if definition.target == "storage" and config.storage.storage.management is None:
            raise ConfigError(
                t("config.storage_management_required", file=config.paths.storage.name, name=name)
            )


def _validate_servers(config: Config) -> None:
    if not config.servers.servers:
        raise ConfigError(t("config.servers_empty", file=config.paths.servers.name))
