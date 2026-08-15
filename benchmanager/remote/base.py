"""リモート実行の抽象(§4.1)。

ドライバと環境パラメータ適用はこのインターフェースだけを使う。
実体は SSH(asyncssh)でもモックでもよく、テストは実機なしで完結する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..types import CommandResult


class RemoteExecutor(ABC):
    """リモートホスト 1 台に対するコマンド実行とファイル転送。"""

    #: ログ表示用の名前(サーバ名など)
    name: str = "remote"

    @abstractmethod
    async def connect(self) -> None:
        """接続を確立する(再入可能であること)。"""

    @abstractmethod
    async def close(self) -> None:
        """接続を閉じる。"""

    @abstractmethod
    async def run(self, command: str, timeout: float | None = None) -> CommandResult:
        """コマンドを実行して結果を返す(非ゼロ終了でも例外にしない)。"""

    @abstractmethod
    async def put_text(self, content: str, remote_path: str) -> None:
        """文字列をリモートのファイルとして書き込む(parmfile 転送に使う)。"""

    @abstractmethod
    async def get_dir(self, remote_dir: str, local_dir: Path) -> None:
        """リモートディレクトリをローカルへ回収する(出力回収に使う)。"""

    # -- 既定実装 ---------------------------------------------------------
    async def mkdir(self, path: str) -> CommandResult:
        return await self.run(f"mkdir -p {path}")

    async def run_checked(self, command: str, timeout: float | None = None) -> CommandResult:
        """非ゼロ終了時に :class:`RemoteError` を送出する。"""
        from ..errors import RemoteError
        from ..i18n import t

        result = await self.run(command, timeout=timeout)
        if not result.ok:
            raise RemoteError(
                t("remote.command_failed", rc=result.exit_code, command=command, output=result.output)
            )
        return result

    async def __aenter__(self) -> "RemoteExecutor":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()
