"""結果ディレクトリ(生データ)の読み書き(§5.2)。

原則: **生データが真実**。SQLite は生データから何度でも再生成できる。
通常のテスト実行も「実行 → 生データ保存 → インポート」の経路を必ず通す。

::

    results/
    └── 2026-07-08_143000_<test-suite-name>/
        ├── manifest.toml
        ├── config/                 # 使用した test/servers/storage.toml のコピー
        ├── params.toml             # 使用した params.toml のコピー
        └── raw/
            ├── <tool>/run-0001/run.toml
            ├── <tool>/run-0001/m-0001/measurement.toml + 生出力
            └── env/                # 環境パラメータ適用の証跡
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import const
from ..errors import StoreError
from ..i18n import t
from ..tomlwriter import dump as toml_dump

MANIFEST_VERSION = 1


def suite_dirname(suite_name: str, created_at: datetime) -> str:
    """結果ディレクトリ名(``2026-07-08_143000_<suite>``)。"""
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in suite_name)
    return f"{created_at:%Y-%m-%d_%H%M%S}_{safe}"


def _run_dirname(index: int) -> str:
    return f"run-{index:04d}"


def _measurement_dirname(seq: int) -> str:
    return f"m-{seq:04d}"


@dataclass
class ResultsLayout:
    """結果ディレクトリのパス計算(書き込み/読み出し共通)。"""

    root: Path
    tool: str

    @property
    def manifest_path(self) -> Path:
        return self.root / const.MANIFEST_FILENAME

    @property
    def raw_dir(self) -> Path:
        return self.root / const.RAW_DIRNAME

    @property
    def tool_dir(self) -> Path:
        return self.raw_dir / self.tool

    @property
    def env_dir(self) -> Path:
        return self.raw_dir / const.ENV_RAW_DIRNAME

    @property
    def config_dir(self) -> Path:
        return self.root / const.CONFIG_COPY_DIRNAME

    def run_dir(self, index: int) -> Path:
        return self.tool_dir / _run_dirname(index)

    def measurement_dir(self, run_index: int, seq: int) -> Path:
        return self.run_dir(run_index) / _measurement_dirname(seq)

    def run_dirs(self) -> list[Path]:
        if not self.tool_dir.is_dir():
            return []
        return sorted(p for p in self.tool_dir.iterdir() if p.is_dir() and p.name.startswith("run-"))

    def measurement_dirs(self, run_dir: Path) -> list[Path]:
        return sorted(p for p in run_dir.iterdir() if p.is_dir() and p.name.startswith("m-"))


class ResultsWriter:
    """テスト実行時に生データを書き出す。"""

    def __init__(self, root: Path, tool: str, suite_name: str, created_at: datetime):
        self.layout = ResultsLayout(root=Path(root), tool=tool)
        self.suite_name = suite_name
        self.created_at = created_at
        self.layout.root.mkdir(parents=True, exist_ok=True)
        self.layout.tool_dir.mkdir(parents=True, exist_ok=True)
        self.layout.env_dir.mkdir(parents=True, exist_ok=True)
        self._env_seq = 0

    @property
    def root(self) -> Path:
        return self.layout.root

    # -- manifest / 設定コピー -------------------------------------------
    def write_manifest(self, extra: dict[str, Any]) -> Path:
        data: dict[str, Any] = {
            "manifest": {
                "version": MANIFEST_VERSION,
                "package": const.PACKAGE_NAME,
                "package_version": const.VERSION,
                "created_at": self.created_at,
                "tool": self.layout.tool,
            },
            "test_suite": {"name": self.suite_name},
        }
        data.update(extra)
        toml_dump(data, self.layout.manifest_path)
        return self.layout.manifest_path

    def copy_config_files(self, paths: list[Path]) -> None:
        self.layout.config_dir.mkdir(parents=True, exist_ok=True)
        for path in paths:
            if Path(path).is_file():
                shutil.copy2(path, self.layout.config_dir / Path(path).name)

    def copy_params_spec(self, path: Path | None) -> None:
        """使用した params.toml をコピーする(再現性。§7)。"""
        if path and Path(path).is_file():
            shutil.copy2(path, self.layout.root / const.PARAMS_COPY_FILENAME)

    # -- テスト条件 -------------------------------------------------------
    def create_run_dir(self, index: int) -> Path:
        path = self.layout.run_dir(index)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_run_meta(
        self,
        index: int,
        conditions: dict[str, Any],
        status: str,
        started_at: datetime | None,
        finished_at: datetime | None,
        error: str = "",
    ) -> Path:
        path = self.layout.run_dir(index) / const.RUN_META_FILENAME
        toml_dump(
            {
                "run": {
                    "index": index,
                    "tool": self.layout.tool,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "error": error,
                },
                "conditions": conditions,
            },
            path,
        )
        return path

    # -- 測定点 -----------------------------------------------------------
    def create_measurement_dir(self, run_index: int, seq: int) -> Path:
        path = self.layout.measurement_dir(run_index, seq)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_measurement_meta(
        self,
        run_index: int,
        seq: int,
        target_iops: int | None,
        status: str,
        started_at: datetime | None,
        finished_at: datetime | None,
        exit_code: int = 0,
        command: str = "",
        error: str = "",
    ) -> Path:
        path = self.layout.measurement_dir(run_index, seq) / const.MEASUREMENT_META_FILENAME
        toml_dump(
            {
                "measurement": {
                    "seq": seq,
                    # target_iops 省略 = 最大負荷計測(iorate=max)
                    "target_iops": target_iops,
                    "max_load": target_iops is None,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "exit_code": exit_code,
                    "command": command,
                    "error": error,
                }
            },
            path,
        )
        return path

    # -- 環境パラメータの証跡 --------------------------------------------
    def write_env_evidence(self, name: str, value: str, content: str) -> Path:
        self._env_seq += 1
        safe_value = "".join(c if c.isalnum() or c in "-_." else "-" for c in str(value))
        path = self.layout.env_dir / f"{self._env_seq:04d}_{name}_{safe_value}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


# --------------------------------------------------------------------------
# 読み出し
# --------------------------------------------------------------------------


def read_toml(path: Path) -> dict[str, Any]:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def read_manifest(results_dir: str | Path) -> dict[str, Any]:
    """結果ディレクトリの manifest.toml を読む。"""
    path = Path(results_dir) / const.MANIFEST_FILENAME
    if not path.is_file():
        raise StoreError(t("importing.manifest_missing", path=path))
    return read_toml(path)
