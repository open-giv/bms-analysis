"""serial_hexdump_logger switches to a new daily file at UTC midnight.

The test shifts the logger's clock with LOGGER_CLOCK_OFFSET_S so that it
starts about 2 seconds before a UTC midnight, then writes bytes on both
sides of the change.
"""
import os
import pty
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import parse_log

REPO = Path(__file__).resolve().parent.parent
BEFORE = bytes.fromhex("01 03 00 00 00 1C 44 03")
AFTER = bytes.fromhex("02 04 00 00 00 15 30 36")


def _utc_day(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_logger_switches_daily_file_at_utc_midnight(tmp_path):
    exe = tmp_path / "logger"
    subprocess.run(["cc", "-O2", "-o", str(exe), str(REPO / "tools/serial_hexdump_logger.c")], check=True)

    now = int(time.time())
    midnight = (now // 86400 + 1) * 86400
    lead = 5                                   # logger clock starts this many seconds before midnight
    offset = midnight - lead - now
    template = str(tmp_path / "%Y-%m-%d" / "wire.log")

    master, slave = pty.openpty()
    proc = subprocess.Popen([str(exe), os.ttyname(slave), template],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env={**os.environ, "LOGGER_CLOCK_OFFSET_S": str(offset)})
    try:
        # Wait for the logger to open the first day's file rather than a fixed sleep, then write
        # BEFORE while its clock is still before midnight and AFTER once it is past.
        day1_file = tmp_path / _utc_day(midnight - 1) / "wire.log"
        deadline = time.time() + lead - 1
        while time.time() < deadline and not day1_file.exists():
            time.sleep(0.02)
        assert day1_file.exists(), "logger did not start before its clock reached midnight"
        os.write(master, BEFORE)
        time.sleep(max(0.0, now + lead + 1.0 - time.time()))
        os.write(master, AFTER)
        time.sleep(0.5)
    finally:
        proc.send_signal(signal.SIGTERM)
        os.write(master, b"\x00")  # unblock read() so the logger sees g_stop
        proc.wait(timeout=3)
        os.close(master)
        os.close(slave)

    day1 = tmp_path / _utc_day(midnight - 1) / "wire.log"
    day2 = tmp_path / _utc_day(midnight) / "wire.log"
    stream1, ts1 = parse_log.load_byte_stream(str(day1))
    stream2, ts2 = parse_log.load_byte_stream(str(day2))
    assert stream1 == BEFORE
    assert stream2.startswith(AFTER)
    assert ts1[0].strftime("%Y-%m-%d") == _utc_day(midnight - 1)
    assert ts2[0].strftime("%Y-%m-%d") == _utc_day(midnight)
