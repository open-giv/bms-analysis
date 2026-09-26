"""serial_hexdump_logger exits non-zero when the serial device goes away.

With VMIN=1 a read only returns 0 or fails when the device has hung up, e.g. the USB dongle was
unplugged. The logger used to spin on a 0-byte read, or break out and exit 0, so systemd logged
a clean stop. Closing the pty master is the same hangup as far as the logger can tell.
"""
import os
import pty
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")


def test_logger_exits_non_zero_when_device_hangs_up(tmp_path):
    exe = tmp_path / "logger"
    subprocess.run(["cc", "-O2", "-o", str(exe), str(REPO / "tools/serial_hexdump_logger.c")], check=True)
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    log = tmp_path / "wire.log"
    proc = subprocess.Popen([str(exe), slave_name, str(log)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not log.exists():   # opened after the serial set-up
            time.sleep(0.02)
        os.write(master, bytes.fromhex("01 03 00 00 00 1C 44 03"))
        while time.time() < deadline and not (log.exists() and log.stat().st_size):
            time.sleep(0.02)
        assert log.exists() and log.stat().st_size, "logger did not log the first bytes"
        os.close(slave)                  # the logger has its own descriptor
        slave = None
        os.close(master)                 # hang up
        master = None
        rc = proc.wait(timeout=5)
    finally:
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert rc != 0, proc.stderr.read().decode()
