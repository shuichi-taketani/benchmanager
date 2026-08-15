"""環境パラメータの適用と検証(§6.5)。

* 値の切り替え時に ``commands`` を順次実行 → ``verify`` の終了コード 0 を確認してから測定へ進む
* verify 失敗はデフォルトでテスト全体を停止(:class:`EnvParamError`)
* 適用したコマンドと verify の出力は証跡として ``raw/env/`` に保存する
* 現在値と同じなら再適用しない(``order`` によるループ順最適化と合わせて切替回数を最小化)
* ``target = "storage"`` も SSH 経由。実行部は :class:`RemoteExecutor` で抽象化してあるため
  将来 REST API 実装に差し替えられる
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config.models import EnvParamApply, EnvParamDef
from ..errors import EnvParamError
from ..i18n import t
from ..remote.base import RemoteExecutor
from ..types import CommandResult

logger = logging.getLogger(__name__)

TARGET_SERVER = "server"
TARGET_STORAGE = "storage"


@dataclass
class EnvApplyRecord:
    """1 パラメータ 1 値の適用結果(証跡用)。"""

    name: str
    value: str
    target: str
    skipped: bool = False
    results: list[CommandResult] = field(default_factory=list)
    verify: CommandResult | None = None

    def as_text(self) -> str:
        lines = [f"# env_param: {self.name}={self.value} (target={self.target})"]
        if self.skipped:
            lines.append("# skipped: already applied")
        for result in self.results:
            lines.append(f"$ {result.command}")
            lines.append(f"# exit={result.exit_code}")
            if result.output:
                lines.append(result.output)
        if self.verify is not None:
            lines.append(f"$ (verify) {self.verify.command}")
            lines.append(f"# exit={self.verify.exit_code}")
            if self.verify.output:
                lines.append(self.verify.output)
        return "\n".join(lines) + "\n"


class EnvParamApplier:
    """環境パラメータの現在値を保持し、変化した分だけ適用する。"""

    def __init__(
        self,
        definitions: dict[str, EnvParamDef],
        executors: dict[str, list[RemoteExecutor]],
        evidence_writer: Callable[[str, str, str], Any] | None = None,
    ):
        self.definitions = definitions
        self.executors = executors
        self.evidence_writer = evidence_writer
        self.current: dict[str, str] = {}
        #: 実際に適用(切替)を行った回数
        self.switch_count = 0

    async def apply_all(self, values: dict[str, Any]) -> list[EnvApplyRecord]:
        """与えられた環境パラメータ値の組み合わせを適用する。

        定義順(``order`` 昇順)に処理する。値が現在値と同じものはスキップする。
        """
        records: list[EnvApplyRecord] = []
        for name, value in values.items():
            definition = self.definitions.get(name)
            if definition is None:
                continue
            record = await self.apply_one(name, definition, str(value))
            records.append(record)
        return records

    async def apply_one(self, name: str, definition: EnvParamDef, value: str) -> EnvApplyRecord:
        if self.current.get(name) == value:
            logger.debug(t("envparams.apply_skip", name=name, value=value))
            return EnvApplyRecord(name=name, value=value, target=definition.target, skipped=True)

        logger.info(t("envparams.apply", name=name, value=value, target=definition.target))
        apply = definition.apply.get(value)
        if apply is None:
            raise EnvParamError(
                t("config.env_value_not_defined", file="test.toml", name=name, value=value)
            )

        record = EnvApplyRecord(name=name, value=value, target=definition.target)
        executors = self._executors_for(name, definition.target)
        for command in self._commands_for(apply, value):
            logger.debug(t("envparams.command", command=command))
            for executor in executors:
                result = await executor.run(command)
                record.results.append(result)
                if not result.ok:
                    self._write_evidence(record)
                    raise EnvParamError(
                        t(
                            "envparams.command_failed",
                            name=name,
                            value=value,
                            rc=result.exit_code,
                            command=command,
                            output=result.output,
                        )
                    )

        if apply.verify:
            for executor in executors:
                verify = await executor.run(apply.verify)
                record.verify = verify
                if not verify.ok:
                    self._write_evidence(record)
                    raise EnvParamError(
                        t(
                            "envparams.verify_failed",
                            name=name,
                            value=value,
                            rc=verify.exit_code,
                            command=apply.verify,
                            output=verify.output,
                        )
                    )
            logger.debug(t("envparams.verify_ok", command=apply.verify))

        self.current[name] = value
        self.switch_count += 1
        # 他パラメータの再適用が必要な場合はその現在値を無効化する
        for other in apply.post_apply:
            self.current.pop(other, None)
        self._write_evidence(record)
        return record

    # -- 内部 -------------------------------------------------------------
    @staticmethod
    def _commands_for(apply: EnvParamApply, value: str) -> list[str]:
        """複雑な手順は外部スクリプトに委譲できる(``script``)。"""
        commands = list(apply.commands)
        if apply.script:
            commands.append(apply.script.format(value=value))
        return commands

    def _executors_for(self, name: str, target: str) -> list[RemoteExecutor]:
        executors = self.executors.get(target) or []
        if not executors:
            raise EnvParamError(t("envparams.no_executor", name=name, target=target))
        return executors

    def _write_evidence(self, record: EnvApplyRecord) -> None:
        if self.evidence_writer is not None:
            self.evidence_writer(record.name, record.value, record.as_text())
