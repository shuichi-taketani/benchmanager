"""SSH 実行層のテスト。

実接続はせず asyncssh をモックする(受け入れ基準 2/7: ネットワーク不要)。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from benchmanager.errors import RemoteError
from benchmanager.remote.ssh import SSHRemoteExecutor


class FakeProcess:
    def __init__(self, exit_status=0, stdout="ok", stderr=""):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class FakeSFTP:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def makedirs(self, path, exist_ok=False):
        self.conn.made_dirs.append(path)

    def open(self, path, mode):
        conn = self.conn

        class Handle:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

            async def write(self_inner, content):
                conn.written[path] = content

        return Handle()

    async def get(self, remote, local, recurse=False):
        target = Path(local) / "output"
        target.mkdir(parents=True, exist_ok=True)
        (target / "flatfile.html").write_text("downloaded", encoding="utf-8")
        self.conn.downloads.append((remote, str(local)))


class FakeConnection:
    def __init__(self, fail_once: bool = False):
        self.commands: list[str] = []
        self.written: dict[str, str] = {}
        self.made_dirs: list[str] = []
        self.downloads: list[tuple[str, str]] = []
        self.closed = False
        self._fail_once = fail_once

    async def run(self, command, timeout=None, check=False):
        if self._fail_once:
            self._fail_once = False
            raise ConnectionResetError("connection lost")
        self.commands.append(command)
        return FakeProcess()

    def start_sftp_client(self):
        return FakeSFTP(self)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.fixture
def fake_asyncssh(monkeypatch):
    """``import asyncssh`` を差し替える。"""
    state = {"connections": [], "fail_once": False, "raise_on_connect": None}

    async def connect(host, **options):
        if state["raise_on_connect"]:
            raise state["raise_on_connect"]
        conn = FakeConnection(fail_once=state["fail_once"])
        state["fail_once"] = False
        state["connections"].append((host, options, conn))
        return conn

    module = types.ModuleType("asyncssh")
    module.connect = connect
    monkeypatch.setitem(sys.modules, "asyncssh", module)
    return state


async def test_run_command(fake_asyncssh):
    executor = SSHRemoteExecutor("host1", "bench", ssh_key="~/.ssh/id_ed25519", port=2222)
    result = await executor.run("echo hi")
    assert result.ok and result.stdout == "ok"
    host, options, conn = fake_asyncssh["connections"][0]
    assert host == "host1"
    assert options["username"] == "bench"
    assert options["port"] == 2222
    assert str(Path("~/.ssh/id_ed25519").expanduser()) in options["client_keys"]
    assert conn.commands == ["echo hi"]
    await executor.close()
    assert conn.closed


async def test_reconnects_after_disconnection(fake_asyncssh):
    """SSH 切断時は再接続を試みる(§4.5)。"""
    fake_asyncssh["fail_once"] = True
    executor = SSHRemoteExecutor("host1", "bench")
    result = await executor.run("uptime")
    assert result.ok
    assert len(fake_asyncssh["connections"]) == 2  # 再接続している


async def test_connect_failure_raises_remote_error(fake_asyncssh):
    fake_asyncssh["raise_on_connect"] = OSError("no route to host")
    executor = SSHRemoteExecutor("host1", "bench")
    with pytest.raises(RemoteError) as exc:
        await executor.connect()
    assert "host1" in str(exc.value)


async def test_put_text_and_get_dir(fake_asyncssh, tmp_path):
    executor = SSHRemoteExecutor("host1", "bench")
    await executor.put_text("parmfile body", "/tmp/benchman/run/parmfile")
    await executor.get_dir("/tmp/benchman/run/output", tmp_path / "local")
    _host, _options, conn = fake_asyncssh["connections"][0]
    assert conn.written["/tmp/benchman/run/parmfile"] == "parmfile body"
    assert conn.made_dirs == ["/tmp/benchman/run"]
    assert (tmp_path / "local" / "output" / "flatfile.html").read_text() == "downloaded"


async def test_run_checked_raises_on_nonzero(fake_asyncssh, monkeypatch):
    executor = SSHRemoteExecutor("host1", "bench")
    await executor.connect()

    async def failing_run(command, timeout=None, check=False):
        return FakeProcess(exit_status=2, stdout="", stderr="boom")

    executor._conn.run = failing_run
    with pytest.raises(RemoteError) as exc:
        await executor.run_checked("false")
    assert "boom" in str(exc.value)
