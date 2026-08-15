"""環境パラメータの適用(§6.5、受け入れ基準 4)。"""

from __future__ import annotations

import pytest

from benchmanager.config.models import EnvParamApply, EnvParamDef
from benchmanager.envparams.applier import TARGET_SERVER, TARGET_STORAGE, EnvParamApplier
from benchmanager.errors import EnvParamError
from benchmanager.remote.mock import MockRemoteExecutor
from benchmanager.types import CommandResult


def mount_definition() -> EnvParamDef:
    return EnvParamDef(
        target="server",
        order=1,
        values=["default", "nconnect"],
        apply={
            "default": EnvParamApply(
                commands=["umount /mnt/bench || true", "mount -t nfs -o vers=4.1 s:/v /mnt/bench"],
                verify="mount | grep -q 'vers=4.1'",
            ),
            "nconnect": EnvParamApply(
                commands=["umount /mnt/bench || true", "mount -t nfs -o nconnect=8 s:/v /mnt/bench"],
                verify="mount | grep -q 'nconnect=8'",
            ),
        },
    )


@pytest.fixture
def applier_and_remote():
    remote = MockRemoteExecutor("bench1")
    evidence: list[tuple[str, str, str]] = []
    applier = EnvParamApplier(
        definitions={"mount_opts": mount_definition()},
        executors={TARGET_SERVER: [remote], TARGET_STORAGE: []},
        evidence_writer=lambda name, value, text: evidence.append((name, value, text)),
    )
    return applier, remote, evidence


async def test_apply_runs_commands_then_verify(applier_and_remote):
    applier, remote, evidence = applier_and_remote
    records = await applier.apply_all({"mount_opts": "nconnect"})

    assert remote.commands == [
        "umount /mnt/bench || true",
        "mount -t nfs -o nconnect=8 s:/v /mnt/bench",
        "mount | grep -q 'nconnect=8'",  # verify は最後
    ]
    assert records[0].verify is not None and records[0].verify.ok
    assert applier.switch_count == 1
    # 証跡が残る(raw/env/ 相当)
    assert evidence and "verify" in evidence[0][2]


async def test_unchanged_value_is_not_reapplied(applier_and_remote):
    applier, remote, _ = applier_and_remote
    await applier.apply_all({"mount_opts": "default"})
    count = len(remote.commands)
    records = await applier.apply_all({"mount_opts": "default"})
    assert records[0].skipped is True
    assert len(remote.commands) == count
    assert applier.switch_count == 1


async def test_command_failure_stops_the_test(applier_and_remote):
    applier, remote, _ = applier_and_remote
    remote.add_response("mount -t nfs", CommandResult(command="", exit_code=32, stderr="busy"))
    with pytest.raises(EnvParamError) as exc:
        await applier.apply_all({"mount_opts": "default"})
    assert "busy" in str(exc.value)


async def test_verify_failure_stops_the_test(applier_and_remote):
    applier, remote, evidence = applier_and_remote
    remote.add_response("grep -q", CommandResult(command="", exit_code=1))
    with pytest.raises(EnvParamError) as exc:
        await applier.apply_all({"mount_opts": "nconnect"})
    assert "verify" in str(exc.value)
    assert evidence  # 失敗時も証跡を残す


async def test_post_apply_invalidates_other_param():
    remote = MockRemoteExecutor("bench1")
    definitions = {
        "outer": EnvParamDef(
            order=1,
            values=["a", "b"],
            apply={
                "a": EnvParamApply(commands=["set-outer a"], post_apply=["inner"]),
                "b": EnvParamApply(commands=["set-outer b"], post_apply=["inner"]),
            },
        ),
        "inner": EnvParamDef(
            order=2,
            values=["x"],
            apply={"x": EnvParamApply(commands=["set-inner x"])},
        ),
    }
    applier = EnvParamApplier(definitions, {TARGET_SERVER: [remote]})
    await applier.apply_all({"outer": "a", "inner": "x"})
    await applier.apply_all({"outer": "b", "inner": "x"})
    # outer が変わったので inner も再適用される
    assert remote.commands.count("set-inner x") == 2


async def test_storage_target_uses_storage_executor():
    server = MockRemoteExecutor("bench1")
    storage = MockRemoteExecutor("a900")
    definitions = {
        "pool": EnvParamDef(
            target="storage",
            values=["fast"],
            apply={"fast": EnvParamApply(commands=["storage set pool fast"])},
        )
    }
    applier = EnvParamApplier(definitions, {TARGET_SERVER: [server], TARGET_STORAGE: [storage]})
    await applier.apply_all({"pool": "fast"})
    assert storage.commands == ["storage set pool fast"]
    assert server.commands == []


async def test_missing_executor_raises():
    definitions = {
        "pool": EnvParamDef(
            target="storage",
            values=["fast"],
            apply={"fast": EnvParamApply(commands=["x"])},
        )
    }
    applier = EnvParamApplier(definitions, {TARGET_SERVER: [MockRemoteExecutor()], TARGET_STORAGE: []})
    with pytest.raises(EnvParamError):
        await applier.apply_all({"pool": "fast"})


async def test_script_receives_value():
    remote = MockRemoteExecutor("bench1")
    definitions = {
        "tuning": EnvParamDef(
            values=["aggressive"],
            apply={"aggressive": EnvParamApply(script="scripts/tune.sh {value}")},
        )
    }
    applier = EnvParamApplier(definitions, {TARGET_SERVER: [remote]})
    await applier.apply_all({"tuning": "aggressive"})
    assert remote.commands == ["scripts/tune.sh aggressive"]


async def test_applies_to_all_servers():
    remotes = [MockRemoteExecutor("bench1"), MockRemoteExecutor("bench2")]
    definitions = {
        "mount_opts": EnvParamDef(
            values=["default"],
            apply={"default": EnvParamApply(commands=["mount x"], verify="true")},
        )
    }
    applier = EnvParamApplier(definitions, {TARGET_SERVER: remotes})
    await applier.apply_all({"mount_opts": "default"})
    assert all(remote.commands == ["mount x", "true"] for remote in remotes)
