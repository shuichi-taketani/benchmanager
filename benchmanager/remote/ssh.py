"""asyncssh によるリモート実行(§2, §4.5)。

* 複数サーバ同時実行(Phase 2)を見据えて非同期
* 切断時は 1 度だけ再接続を試みる。それでも失敗したら :class:`RemoteError`
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..errors import RemoteError
from ..i18n import t
from ..types import CommandResult
from .base import RemoteExecutor

logger = logging.getLogger(__name__)


class SSHRemoteExecutor(RemoteExecutor):
    """asyncssh を用いた実装。"""

    def __init__(
        self,
        host: str,
        user: str,
        *,
        name: str | None = None,
        ssh_key: str | None = None,
        password: str | None = None,
        port: int = 22,
        known_hosts: object = None,
        connect_timeout: float = 30.0,
    ):
        self.host = host
        self.user = user
        self.name = name or host
        self.ssh_key = ssh_key
        self.password = password
        self.port = port
        # ラボ環境では known_hosts を持たないことが多いため既定は無効
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout
        self._conn = None

    @classmethod
    def from_server_config(cls, server) -> "SSHRemoteExecutor":
        """``servers.toml`` の 1 エントリから生成する。"""
        return cls(
            host=server.host,
            user=server.user,
            name=server.name,
            ssh_key=server.ssh_key,
            password=server.password,
            port=server.port,
        )

    @classmethod
    def from_management_config(cls, management, name: str = "storage") -> "SSHRemoteExecutor":
        """``storage.toml`` の ``[storage.management]`` から生成する。"""
        return cls(
            host=management.host,
            user=management.user,
            name=name,
            ssh_key=management.ssh_key,
            password=management.password,
            port=management.port,
        )

    # -- 接続 -------------------------------------------------------------
    async def connect(self) -> None:
        if self._conn is not None:
            return
        import asyncssh

        logger.debug(t("remote.connect", user=self.user, host=self.host))
        options: dict = {
            "username": self.user,
            "port": self.port,
            "known_hosts": self.known_hosts,
            "connect_timeout": self.connect_timeout,
        }
        if self.ssh_key:
            options["client_keys"] = [str(Path(self.ssh_key).expanduser())]
        if self.password:
            options["password"] = self.password
        try:
            self._conn = await asyncssh.connect(self.host, **options)
        except Exception as exc:  # asyncssh の例外階層に依存しない
            raise RemoteError(t("remote.connect_failed", host=self.host, message=exc)) from exc

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            finally:
                self._conn = None

    async def _reconnect(self) -> None:
        logger.warning(t("remote.reconnect", host=self.host))
        await self.close()
        await self.connect()

    # -- 実行 -------------------------------------------------------------
    async def run(self, command: str, timeout: float | None = None) -> CommandResult:
        await self.connect()
        try:
            result = await self._conn.run(command, timeout=timeout, check=False)
        except Exception:
            # 切断とみなして 1 度だけ再接続を試みる(§4.5)
            await self._reconnect()
            result = await self._conn.run(command, timeout=timeout, check=False)
        return CommandResult(
            command=command,
            exit_code=int(result.exit_status or 0),
            stdout=str(result.stdout or ""),
            stderr=str(result.stderr or ""),
        )

    # -- ファイル転送 -----------------------------------------------------
    async def put_text(self, content: str, remote_path: str) -> None:
        await self.connect()
        logger.debug(t("remote.upload", path=remote_path))
        async with self._conn.start_sftp_client() as sftp:
            parent = str(Path(remote_path).parent)
            if parent not in ("", "/"):
                try:
                    await sftp.makedirs(parent, exist_ok=True)
                except Exception:  # 既存/権限差異は無視して書き込みで判定させる
                    pass
            async with sftp.open(remote_path, "w") as fh:
                await fh.write(content)

    async def get_dir(self, remote_dir: str, local_dir: Path) -> None:
        await self.connect()
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(t("remote.download", path=remote_dir))
        async with self._conn.start_sftp_client() as sftp:
            await sftp.get(remote_dir, str(local_dir), recurse=True)
