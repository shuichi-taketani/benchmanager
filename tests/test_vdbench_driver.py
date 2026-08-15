"""vdbench ドライバの結合テスト(受け入れ基準 2)。

parmfile 生成 → 転送 → 実行 → flatfile 回収 → パース を、実機なしで通す。
"""

from __future__ import annotations

import pytest

from benchmanager import metrics as M
from benchmanager.config import load_config
from benchmanager.drivers.vdbench.driver import VdbenchDriver
from benchmanager.drivers.vdbench.parser import FLATFILE_NAME, HISTOGRAM_NAME
from benchmanager.errors import MeasurementError
from benchmanager.remote.mock import MockRemoteExecutor
from benchmanager.types import CommandResult, RawResult, Timing

from .conftest import VDBENCH_OUTPUT

VDBENCH_TEST_TOML = """
[test]
tool = "vdbench"
test_suite = "vdbench-unit"

[strategy]
type = "vdbench_linear_descent"

[strategy.max_load]
mode = "measure"
latency_threshold_ms = 5.0

[strategy.params]
step_iops = 10000
stop_delta_pct = 5
max_iterations = 3

[timing]
duration_sec = 600
warmup_sec = 60
interval_sec = 1

[tool_params]
threads = 256
block_size = "4k"
read_pct = 100
compratio = 4

[tool_params.raw]
messagescan = "no"
"""

VDBENCH_SERVERS_TOML = """
[[servers]]
name = "bench1"
host = "bench1.example.com"
user = "bench"
vdbench_path = "/opt/vdbench"
workdir = "/tmp/benchman"

[[servers]]
name = "bench2"
host = "bench2.example.com"
user = "bench"
vdbench_path = "/opt/vdbench"
"""

VDBENCH_STORAGE_TOML = """
[storage]
name = "a900"
luns = ["/mnt/bench/file1", "/mnt/bench/file2"]
"""


@pytest.fixture
def vdbench_config(make_config_dir):
    directory = make_config_dir(
        VDBENCH_TEST_TOML,
        servers_toml=VDBENCH_SERVERS_TOML,
        storage_toml=VDBENCH_STORAGE_TOML,
    )
    return load_config(directory)


@pytest.fixture
def vdbench_remote() -> MockRemoteExecutor:
    """flatfile / histogram を返すモックリモート。"""
    remote = MockRemoteExecutor(name="bench1")
    remote.add_file(
        f"output/{FLATFILE_NAME}",
        (VDBENCH_OUTPUT / FLATFILE_NAME).read_text(encoding="utf-8", errors="replace"),
    )
    remote.add_file(
        f"output/{HISTOGRAM_NAME}",
        (VDBENCH_OUTPUT / HISTOGRAM_NAME).read_text(encoding="utf-8", errors="replace"),
    )
    return remote


def test_generate_parmfile(vdbench_config):
    driver = VdbenchDriver(config=vdbench_config, server=vdbench_config.primary_server())
    text = driver.generate_config(vdbench_config.tool_params, target_iops=100000)

    assert "hd=default,shell=ssh,jvms=16" in text
    assert "hd=hd1,system=bench1.example.com,user=bench" in text
    assert "hd=hd2,system=bench2.example.com,user=bench" in text
    assert "sd=default,size=256g,openflag=o_direct" in text
    assert "sd=sd1-1,host=hd1,lun=/mnt/bench/file1" in text
    assert "wd=wd1,sd=*,seekpct=100,xfersize=4k,rdpct=100" in text
    assert "rd=default,warmup=60,elapsed=600,interval=1" in text
    assert "rd=rd1,wd=wd1,iorate=100000,threads=256" in text
    assert "compratio=4" in text
    # [tool_params.raw] は無検証でそのまま渡す
    assert "messagescan=no" in text


def test_generate_parmfile_max_load(vdbench_config):
    driver = VdbenchDriver(config=vdbench_config, server=vdbench_config.primary_server())
    text = driver.generate_config(vdbench_config.tool_params, target_iops=None)
    assert "iorate=max" in text


async def test_run_transfers_executes_and_collects(vdbench_config, vdbench_remote, tmp_path):
    driver = VdbenchDriver(config=vdbench_config, server=vdbench_config.primary_server())
    parmfile = driver.generate_config(vdbench_config.tool_params, target_iops=470000)
    output_dir = tmp_path / "run-0001" / "m-0001"

    raw = await driver.run(vdbench_remote, parmfile, Timing(600, 60, 1), output_dir)

    # parmfile がリモートへ転送されている
    uploaded = list(vdbench_remote.uploads.values())
    assert uploaded and "iorate=470000" in uploaded[0]
    # vdbench コマンドが実行されている
    assert any("/opt/vdbench/vdbench -f" in cmd for cmd in vdbench_remote.commands)
    assert any("mkdir -p /tmp/benchman/" in cmd for cmd in vdbench_remote.commands)
    # 出力が回収され、生データとして保存されている
    assert (output_dir / "output" / FLATFILE_NAME).is_file()
    assert (output_dir / "parmfile").is_file()
    assert raw.ok


async def test_run_and_parse_roundtrip(vdbench_config, vdbench_remote, tmp_path):
    driver = VdbenchDriver(config=vdbench_config, server=vdbench_config.primary_server())
    parmfile = driver.generate_config(vdbench_config.tool_params, target_iops=470000)
    raw = await driver.run(vdbench_remote, parmfile, Timing(600, 60, 1), tmp_path / "m-0001")

    values = {metric.name: metric.value for metric in driver.parse(raw)}
    assert values[M.IOPS] == pytest.approx(474700.05, rel=1e-6)
    assert values[M.LATENCY_AVG] == pytest.approx(5.3081, rel=1e-6)
    assert M.LATENCY_P95 in values  # histogram からのパーセンタイル
    assert driver.parse_timeseries(raw)


async def test_run_failure_raises_measurement_error(vdbench_config, vdbench_remote, tmp_path):
    vdbench_remote.add_response(
        "vdbench -f", CommandResult(command="", exit_code=3, stderr="java not found")
    )
    driver = VdbenchDriver(config=vdbench_config, server=vdbench_config.primary_server())
    parmfile = driver.generate_config(vdbench_config.tool_params, target_iops=1000)
    with pytest.raises(MeasurementError):
        await driver.run(vdbench_remote, parmfile, Timing(10, 1, 1), tmp_path / "m-0001")
    # 失敗時も stdout は証跡として残す
    assert (tmp_path / "m-0001" / "stdout.log").is_file()


def test_parse_without_flatfile_raises(tmp_path):
    driver = VdbenchDriver()
    raw = RawResult(directory=tmp_path, tool="vdbench")
    with pytest.raises(MeasurementError):
        driver.parse(raw)


def test_no_luns_is_rejected(make_config_dir):
    from benchmanager.errors import DriverError

    directory = make_config_dir(
        VDBENCH_TEST_TOML,
        servers_toml=VDBENCH_SERVERS_TOML,
        storage_toml='[storage]\nname = "empty"\n',
    )
    config = load_config(directory)
    driver = VdbenchDriver(config=config, server=config.primary_server())
    with pytest.raises(DriverError):
        driver.generate_config(config.tool_params, target_iops=1000)
