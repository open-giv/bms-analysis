"""Wire and TCP timestamps must end up on the same clock (UTC) after loading.

serial_hexdump_logger used to write local time with no zone while tcp_poller
writes UTC, so a join shifted every TCP value by the local UTC offset.
"""
import json
import os
import pty
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from tools import parse_log
from tools.join_streams import load_tcp_records, load_wire_records

REPO = Path(__file__).resolve().parent.parent
HR_REQUEST = bytes.fromhex("01 03 00 00 00 1C 44 03")


def _hr_response():
    body = bytes([0x01, 0x03, 0x38]) + (REPO / "tests/fixtures/sample_hr_response.bin").read_bytes()
    crc = parse_log.crc16(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def _log_line(ts, offset, data):
    hexpart = " ".join(f"{b:02X}" for b in data)
    return f"{ts}  {offset:08x}  {hexpart}  |{'.' * len(data)}|\n"


def _write_log(path, request_ts, response_ts):
    rsp = _hr_response()
    lines = [_log_line(request_ts, 0, HR_REQUEST)]
    for i in range(0, len(rsp), 16):
        lines.append(_log_line(response_ts, len(HR_REQUEST) + i, rsp[i:i + 16]))
    path.write_text("".join(lines))


def test_load_byte_stream_reads_utc_suffix_as_aware(tmp_path):
    log = tmp_path / "wire.log"
    _write_log(log, "2026-08-21 16:18:04.389Z", "2026-08-21 16:18:04.489Z")
    stream, timestamps = parse_log.load_byte_stream(str(log))
    assert len(stream) == len(HR_REQUEST) + 61
    assert timestamps[-1] == datetime(2026, 8, 21, 16, 18, 4, 489000, tzinfo=timezone.utc)


def test_load_wire_records_utc_log_is_utc(tmp_path):
    log = tmp_path / "wire.log"
    _write_log(log, "2026-08-21 16:18:04.389Z", "2026-08-21 16:18:04.489Z")
    wire = load_wire_records(log)
    assert wire["ts"].iloc[0] == pd.Timestamp("2026-08-21 16:18:04.489", tz="UTC")


def test_load_wire_records_localises_zoneless_log_with_wire_tz(tmp_path):
    log = tmp_path / "wire.log"
    # Old logger output: BST wall-clock time, no zone marker.
    _write_log(log, "2026-08-21 17:18:04.389", "2026-08-21 17:18:04.489")
    wire = load_wire_records(log, wire_tz="Europe/London")
    assert wire["ts"].iloc[0] == pd.Timestamp("2026-08-21 16:18:04.489", tz="UTC")


def test_load_wire_records_rejects_zoneless_log_without_wire_tz(tmp_path):
    log = tmp_path / "wire.log"
    _write_log(log, "2026-08-21 17:18:04.389", "2026-08-21 17:18:04.489")
    with pytest.raises(ValueError, match="wire-tz"):
        load_wire_records(log)


def test_load_tcp_records_is_utc(tmp_path):
    tcp = tmp_path / "tcp.ndjson"
    tcp.write_text(json.dumps({"ts": "2026-08-21T16:18:04.000000+00:00", "fields": {"soc": 50}}) + "\n")
    df = load_tcp_records(tcp)
    assert df["ts"].iloc[0] == pd.Timestamp("2026-08-21 16:18:04", tz="UTC")
    assert str(df["ts"].dt.tz) == "UTC"


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_serial_hexdump_logger_writes_utc_timestamps(tmp_path):
    exe = tmp_path / "logger"
    subprocess.run(["cc", "-O2", "-o", str(exe), str(REPO / "tools/serial_hexdump_logger.c")], check=True)
    master, slave = pty.openpty()
    log = tmp_path / "wire.log"
    proc = subprocess.Popen([str(exe), os.ttyname(slave), str(log)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env={**os.environ, "TZ": "Europe/London"})
    try:
        time.sleep(0.3)
        before = datetime.now(timezone.utc)
        os.write(master, HR_REQUEST)
        deadline = time.time() + 3
        while time.time() < deadline and not (log.exists() and log.read_text()):
            time.sleep(0.05)
    finally:
        proc.send_signal(signal.SIGTERM)
        os.write(master, b"\x00")  # unblock read() so the logger sees g_stop
        proc.wait(timeout=3)
        os.close(master)
        os.close(slave)
    _, timestamps = parse_log.load_byte_stream(str(log))
    assert timestamps, "logger wrote nothing"
    assert timestamps[0].tzinfo is not None
    assert abs(timestamps[0] - before) < timedelta(seconds=5)
