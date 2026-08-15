"""ベンチマークツールの抽象化(§9)。

ツール固有の知識をこの外へ漏らさないことが最重要の設計制約。エンジンは
``generate_config`` / ``run`` / ``parse`` / ``available_strategies`` しか知らない。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..errors import DriverError
from ..i18n import t
from ..params import ParamsSpec, load_params_spec
from ..remote.base import RemoteExecutor
from ..strategy.base import MeasurementStrategy, StrategySpec
from ..strategy.registry import GENERIC_STRATEGY_NAMES, create_generic_strategy, is_generic
from ..types import Metric, RawResult, TimeseriesPoint, Timing


class BenchDriver(ABC):
    """ベンチマークツール 1 種を表すドライバ。

    ``parse()`` は生データディレクトリのみを入力とし、実行状態に依存しないこと
    (``benchman import`` で後から再パースできるようにするため。§5.2)。
    """

    #: 設定ファイルの ``test.tool`` に対応する名前
    tool_name: str = ""
    #: ``target_iops=None``(最大負荷計測)に対応するか(§4.3)
    supports_max_load: bool = False

    _params_spec_cache: dict[str, ParamsSpec] = {}

    def __init__(self, config: Any | None = None, server: Any | None = None):
        #: :class:`benchmanager.config.Config`(パース専用に生成する場合は None)
        self.config = config
        #: 実行先サーバ定義(``servers.toml`` の 1 エントリ)
        self.server = server

    # -- パラメータ定義 ---------------------------------------------------
    @classmethod
    def params_spec_path(cls) -> Path:
        """同梱の ``params.toml`` のパス(§7)。"""
        return Path(inspect_module_dir(cls)) / "params.toml"

    @classmethod
    def load_params_spec(cls) -> ParamsSpec:
        key = cls.tool_name
        if key not in BenchDriver._params_spec_cache:
            BenchDriver._params_spec_cache[key] = load_params_spec(cls.params_spec_path())
        return BenchDriver._params_spec_cache[key]

    # -- 測定戦略 ---------------------------------------------------------
    @classmethod
    def tool_strategies(cls) -> list[str]:
        """このツール固有の戦略名(既定は無し)。"""
        return []

    @classmethod
    def available_strategies(cls) -> list[str]:
        """対応する戦略名。汎用戦略は全ドライバ共通で常に含める(§9)。"""
        return list(GENERIC_STRATEGY_NAMES) + cls.tool_strategies()

    @classmethod
    def create_strategy(cls, name: str, spec: StrategySpec) -> MeasurementStrategy:
        """戦略名から測定戦略を生成する。

        汎用戦略はここで解決し、ツール固有戦略は各ドライバがオーバーライドする。
        """
        if is_generic(name):
            return create_generic_strategy(name, spec)
        raise DriverError(
            t(
                "engine.strategy_unsupported",
                strategy=name,
                tool=cls.tool_name,
                available=", ".join(cls.available_strategies()),
            )
        )

    @classmethod
    def requires_max_load(cls, name: str) -> bool:
        """その戦略が最大負荷計測を必要とするか(エンジンが起動前に判定する)。"""
        strategy_cls = cls.strategy_class(name)
        return bool(getattr(strategy_cls, "requires_max_load", False))

    @classmethod
    def strategy_class(cls, name: str) -> type[MeasurementStrategy]:
        from ..strategy.registry import GENERIC_STRATEGIES

        if name in GENERIC_STRATEGIES:
            return GENERIC_STRATEGIES[name]
        raise DriverError(
            t(
                "engine.strategy_unsupported",
                strategy=name,
                tool=cls.tool_name,
                available=", ".join(cls.available_strategies()),
            )
        )

    # -- 実行先 -----------------------------------------------------------
    @classmethod
    def create_executor(cls, server: Any) -> RemoteExecutor:
        """``servers.toml`` の 1 エントリに対する実行先を作る。

        既定は SSH。疑似ドライバなど実接続が不要なものはここを差し替える
        (エンジンにツール固有の分岐を持ち込まないため)。
        """
        from ..remote.ssh import SSHRemoteExecutor

        return SSHRemoteExecutor.from_server_config(server)

    @classmethod
    def create_management_executor(cls, management: Any, name: str = "storage") -> RemoteExecutor:
        """``[storage.management]`` に対する実行先(env_params の target="storage" 用)。"""
        from ..remote.ssh import SSHRemoteExecutor

        return SSHRemoteExecutor.from_management_config(management, name=name)

    # -- 実行 -------------------------------------------------------------
    @abstractmethod
    def generate_config(self, conditions: dict[str, Any], target_iops: int | None) -> str:
        """測定 1 点分のツール設定(vdbench なら parmfile)を生成する。

        ``target_iops=None`` は最大負荷計測(vdbench の ``iorate=max``)を意味する。
        """

    @abstractmethod
    async def run(
        self,
        remote: RemoteExecutor,
        config: str,
        timing: Timing,
        output_dir: Path,
    ) -> RawResult:
        """リモートで 1 測定点を実行し、生出力を ``output_dir`` に回収する。

        ``output_dir`` は §9 の擬似コードには無いが、生データ保存を経由してから
        パースする方針(§5.2)を満たすために必須の引数。
        """

    @abstractmethod
    def parse(self, raw: RawResult) -> list[Metric]:
        """生出力を正規化された縦持ちメトリクスへ変換する。"""

    def parse_timeseries(self, raw: RawResult) -> list[TimeseriesPoint]:
        """時系列メトリクス(既定は無し)。"""
        return []

    # -- 情報 -------------------------------------------------------------
    def describe_command(self, target_iops: int | None) -> str:
        return f"{self.tool_name} target_iops={'max' if target_iops is None else target_iops}"


def inspect_module_dir(cls: type) -> Path:
    """クラスが定義されたモジュールのディレクトリ。"""
    import inspect

    return Path(inspect.getfile(cls)).resolve().parent
