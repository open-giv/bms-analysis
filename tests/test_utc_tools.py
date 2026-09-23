"""modbus_register_logger and modbus_proxy write UTC timestamps, like serial_hexdump_logger.

They used to write local time with no zone, so their output could not be lined up with the
UTC wire logs and tcp_poller records. Each test runs the compiled tool with TZ=Europe/London so
local time and UTC differ in summer, and feeds it frames over pseudo-terminals.
"""
import os
import pty
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_wire_timestamps import HR_REQUEST, _hr_response

REPO = Path(__file__).resolve().parent.parent
LONDON = {**os.environ, "TZ": "Europe/London"}
pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")


def _compile(tmp_path, name):
    exe = tmp_path / name
    subprocess.run(["cc", "-O2", "-o", str(exe), str(REPO / f"tools/{name}.c")], check=True)
    return exe


def _wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _stop(proc, masters):
    proc.send_signal(signal.SIGTERM)
    for m in masters:
        try:
            os.write(m, b"\x00")  # unblock a pending read() so the tool sees the signal
        except OSError:
            pass
    proc.wait(timeout=5)


def _assert_recent_utc(stamp, before):
    assert stamp.endswith("Z"), stamp
    ts = datetime.fromisoformat(stamp)
    assert abs(ts - before) < timedelta(seconds=10), (stamp, before)


def test_register_logger_writes_utc_timestamps(tmp_path):
    exe = _compile(tmp_path, "modbus_register_logger")
    master, slave = pty.openpty()
    log = tmp_path / "reg.log"
    proc = subprocess.Popen([str(exe), os.ttyname(slave), "holding", "21", str(log)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=LONDON)
    try:
        assert _wait_for(log.exists), "register logger did not start"   # it opens the log at start-up
        before = datetime.now(timezone.utc)
        os.write(master, HR_REQUEST)
        time.sleep(0.05)
        os.write(master, _hr_response())
        assert _wait_for(lambda: log.exists() and "holding[21]" in log.read_text()), "no register line logged"
    finally:
        _stop(proc, [master])
        os.close(master); os.close(slave)
    line = next(l for l in log.read_text().splitlines() if "holding[21]" in l)
    assert "=0x005F (95)" in line                     # HR21 SoC from the fixture
    _assert_recent_utc(line.split(" ")[0] + " " + line.split(" ")[1], before)


def _run_proxy(tmp_path, command):
    exe = _compile(tmp_path, "modbus_proxy")
    cm, cs = pty.openpty()          # controller (inverter) side
    bm, bs = pty.openpty()          # bus (battery) side
    err = tmp_path / "proxy.stderr"
    proc = subprocess.Popen([str(exe), os.ttyname(cs), os.ttyname(bs)], cwd=tmp_path, env=LONDON,
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open(err, "w"))
    try:
        assert _wait_for(lambda: "Waiting to detect" in err.read_text()), "proxy did not start"
        os.write(cm, HR_REQUEST)                       # lets the proxy detect the controller port
        time.sleep(0.3)
        proc.stdin.write(command.encode() + b"\n"); proc.stdin.flush()
        time.sleep(0.3)
        before = datetime.now(timezone.utc)
        os.write(cm, HR_REQUEST)
        time.sleep(0.1)
        os.write(bm, _hr_response())
        time.sleep(0.5)
    finally:
        _stop(proc, [cm, bm])
        for fd in (cm, cs, bm, bs):
            os.close(fd)
    return before


def test_proxy_log_lines_are_utc(tmp_path):
    log = tmp_path / "proxy.log"
    before = _run_proxy(tmp_path, f"log start {log}")
    assert log.exists(), "proxy did not create its log"
    stamps = [m.group(1) for m in re.finditer(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}Z?)\s", log.read_text(), re.M)]
    assert stamps, "no hex lines in proxy log"
    for s in stamps:
        _assert_recent_utc(s, before)


def test_proxy_default_log_name_uses_utc(tmp_path):
    started = datetime.now(timezone.utc)
    _run_proxy(tmp_path, "log start")
    names = [p.name for p in tmp_path.glob("proxy_log_*.log")]
    assert len(names) == 1, names
    stamp = datetime.strptime(names[0], "proxy_log_%Y%m%d_%H%M%S.log").replace(tzinfo=timezone.utc)
    assert abs(stamp - started) < timedelta(seconds=30), (names[0], started)
