"""設定ファイルの pydantic モデル(§6)。

3 ファイル(test.toml / servers.toml / storage.toml)それぞれに対応するトップレベル
モデルを持つ。すべて ``extra="forbid"`` とし、未知キーはエラーにする。

意味的な検証(戦略とドライバの整合、matrix の掃引可否、env_params の値定義など)は
ファイル名を添えたエラーメッセージにするため :mod:`benchmanager.config.loader` 側で行う。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """未知キーを許容しない基底モデル。"""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# test.toml
# --------------------------------------------------------------------------


class TestSection(StrictModel):
    """``[test]`` セクション。"""

    tool: str
    test_suite: str
    description: str = ""


class MaxLoadConfig(StrictModel):
    """``[strategy.max_load]``: vdbench 固有戦略の起点となる最大負荷計測(§4.3)。"""

    mode: Literal["measure", "manual"] = "measure"
    latency_threshold_ms: float = 5.0
    manual_iops: int | None = None


class StrategyParams(StrictModel):
    """``[strategy.params]``: 戦略ごとのパラメータ。"""

    step_iops: int | None = None
    stop_delta_pct: float = 10.0
    max_iterations: int = 10


class StrategyConfig(StrictModel):
    """``[strategy]`` セクション。

    汎用戦略 ``range`` は ``start_iops`` / ``end_iops`` / ``step_iops`` の 3 値のみを使う。
    vdbench 固有戦略は ``max_load`` と ``params`` を使う。
    """

    type: str
    start_iops: int | None = None
    end_iops: int | None = None
    step_iops: int | None = None
    max_load: MaxLoadConfig = Field(default_factory=MaxLoadConfig)
    params: StrategyParams = Field(default_factory=StrategyParams)


class TimingConfig(StrictModel):
    """``[timing]`` セクション(§4.1 の既定値)。"""

    duration_sec: int = 600
    warmup_sec: int = 60
    interval_sec: int = 1


class EnvParamApply(StrictModel):
    """``[env_params.<name>.apply.<value>]``: 1 つの値に対する適用手順。"""

    commands: list[str] = Field(default_factory=list)
    verify: str | None = None
    post_apply: list[str] = Field(default_factory=list)
    script: str | None = None


class EnvParamDef(StrictModel):
    """``[env_params.<name>]``: 環境パラメータ定義(§6.1)。"""

    target: Literal["server", "storage"] = "server"
    order: int | None = None
    values: list[str]
    apply: dict[str, EnvParamApply] = Field(default_factory=dict)


class TestConfig(StrictModel):
    """test.toml 全体。"""

    test: TestSection
    strategy: StrategyConfig
    timing: TimingConfig = Field(default_factory=TimingConfig)
    # tool_params のキーは params.toml で動的に検証するため、ここでは緩く受ける
    tool_params: dict[str, Any] = Field(default_factory=dict)
    matrix: dict[str, list[Any]] = Field(default_factory=dict)
    env_params: dict[str, EnvParamDef] = Field(default_factory=dict)

    @property
    def raw_tool_params(self) -> dict[str, Any]:
        """``[tool_params.raw]``(無検証でドライバへ渡す値)。"""
        raw = self.tool_params.get("raw", {})
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def defined_tool_params(self) -> dict[str, Any]:
        """``raw`` を除いたツールパラメータ。"""
        return {k: v for k, v in self.tool_params.items() if k != "raw"}


# --------------------------------------------------------------------------
# servers.toml
# --------------------------------------------------------------------------


class ServerConfig(StrictModel):
    """``[[servers]]``: 負荷サーバ 1 台の定義(§6.2)。"""

    name: str
    host: str
    user: str
    ssh_key: str | None = None
    password: str | None = None
    port: int = 22
    vdbench_path: str | None = None
    workdir: str = "/tmp/benchman"


class ServersConfig(StrictModel):
    """servers.toml 全体。"""

    servers: list[ServerConfig] = Field(default_factory=list)


# --------------------------------------------------------------------------
# storage.toml
# --------------------------------------------------------------------------


class StorageManagement(StrictModel):
    """``[storage.management]``: ストレージ管理接続(ONTAP CLI 等)。"""

    host: str
    user: str
    ssh_key: str | None = None
    password: str | None = None
    port: int = 22


class StorageSection(StrictModel):
    """``[storage]`` セクション(§6.3)。"""

    name: str
    luns: list[str] = Field(default_factory=list)
    description: str = ""
    management: StorageManagement | None = None


class StorageConfig(StrictModel):
    """storage.toml 全体。"""

    storage: StorageSection
