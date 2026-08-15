"""インメモリのモック RemoteExecutor(§10)。

実機・ネットワークなしでエンジン全体をテストするために使う。

* 実行されたコマンドは :attr:`commands` に記録される(env_params の適用回数検証に使う)
* ``responses`` に正規表現→:class:`CommandResult` を登録すると応答を差し替えられる
* ``files`` に「リモートに存在するファイル」を登録しておくと :meth:`get_dir` で回収できる
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ..types import CommandResult
from .base import RemoteExecutor

Responder = Callable[[str], CommandResult] | CommandResult


class MockRemoteExecutor(RemoteExecutor):
    """テスト用のリモート実行。"""

    def __init__(self, name: str = "mock", *, fail_pattern: str | None = None):
        self.name = name
        self.commands: list[str] = []
        self.uploads: dict[str, str] = {}
        #: 回収されるリモートファイル(相対パス -> 内容)
        self.files: dict[str, str] = {}
        self.responses: list[tuple[re.Pattern[str], Responder]] = []
        self.connected = False
        self.connect_count = 0
        self.closed_count = 0
        if fail_pattern:
            self.add_response(fail_pattern, CommandResult(command="", exit_code=1, stderr="mock failure"))

    # -- 設定 -------------------------------------------------------------
    def add_response(self, pattern: str, response: Responder) -> None:
        """コマンドの正規表現に対する応答を登録する(先に登録したものが優先)。"""
        self.responses.append((re.compile(pattern), response))

    def add_file(self, relative_path: str, content: str) -> None:
        """回収対象のリモートファイルを登録する。"""
        self.files[relative_path] = content

    # -- RemoteExecutor ---------------------------------------------------
    async def connect(self) -> None:
        self.connected = True
        self.connect_count += 1

    async def close(self) -> None:
        self.connected = False
        self.closed_count += 1

    async def run(self, command: str, timeout: float | None = None) -> CommandResult:
        self.commands.append(command)
        for pattern, responder in self.responses:
            if pattern.search(command):
                result = responder(command) if callable(responder) else responder
                return CommandResult(
                    command=command,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        return CommandResult(command=command, exit_code=0, stdout="")

    async def put_text(self, content: str, remote_path: str) -> None:
        self.uploads[remote_path] = content

    async def get_dir(self, remote_dir: str, local_dir: Path) -> None:
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        for relative, content in self.files.items():
            target = local_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
