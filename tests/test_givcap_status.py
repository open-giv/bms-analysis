"""givcap_status: last-line parsing and the health report."""
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.givcap_status import last_line_time, report

NOW = datetime(2026, 9, 24, 12, 0, 30, 500000, tzinfo=timezone.utc)
SERVICES = {"givcap-wire": "active since 2026-09-24 08:00:00 UTC",
            "givcap-mqtt": "active since 2026-09-24 08:00:05 UTC"}


def _today(tmp_path):
    d = tmp_path / "2026-09-24"
    d.mkdir()
    (d / "wire.log").write_text(
        "2026-09-24 12:00:00.100Z  00000000  01 03 00 00 00 1C 44 03                          |......D.|\n"
        "2026-09-24 12:00:25.500Z  00000008  01 03 00 00 00 1C 44 03                          |......D.|\n")
    (d / "tcp.ndjson").write_text(json.dumps({"ts": "2026-09-24T12:00:20+00:00", "fields": {"soc": 50}}) + "\n")
    return d


def test_last_line_time_wire_log(tmp_path):
    d = _today(tmp_path)
    assert last_line_time(d / "wire.log") == datetime(2026, 9, 24, 12, 0, 25, 500000, tzinfo=timezone.utc)


def test_last_line_time_ndjson(tmp_path):
    d = _today(tmp_path)
    assert last_line_time(d / "tcp.ndjson") == datetime(2026, 9, 24, 12, 0, 20, tzinfo=timezone.utc)


def test_last_line_time_missing_or_empty(tmp_path):
    assert last_line_time(tmp_path / "nope.log") is None
    (tmp_path / "empty.log").write_text("")
    assert last_line_time(tmp_path / "empty.log") is None


def test_last_line_time_skips_torn_last_line(tmp_path):
    p = tmp_path / "tcp.ndjson"
    p.write_text(json.dumps({"ts": "2026-09-24T12:00:20+00:00", "fields": {}}) + "\n{\"ts\": \"2026-09")
    assert last_line_time(p) == datetime(2026, 9, 24, 12, 0, 20, tzinfo=timezone.utc)


def test_report_shows_services_ages_disk_and_clock(tmp_path):
    _today(tmp_path)
    text = "\n".join(report(tmp_path, NOW, SERVICES, free_bytes=20 * 10**9, clock_synced="yes"))
    assert "givcap-wire: active since 2026-09-24 08:00:00 UTC" in text
    assert "wire.log: last line 2026-09-24 12:00:25 UTC (5 s ago)" in text
    assert "tcp.ndjson: last line 2026-09-24 12:00:20 UTC (10 s ago)" in text
    assert "disk free: 20.0 GB" in text
    assert "WARNING" not in text
    assert "clock synchronised: yes" in text


def test_report_warns_on_low_disk(tmp_path):
    _today(tmp_path)
    text = "\n".join(report(tmp_path, NOW, SERVICES, free_bytes=1_500_000_000, clock_synced="yes"))
    assert "WARNING: less than 2 GB free" in text


def test_report_handles_missing_files(tmp_path):
    text = "\n".join(report(tmp_path, NOW, SERVICES, free_bytes=20 * 10**9, clock_synced="no"))
    assert "wire.log: no file yet for 2026-09-24" in text
    assert "tcp.ndjson: no file yet for 2026-09-24" in text
    assert "clock synchronised: no" in text


def test_last_line_time_finds_line_longer_than_tail_window(tmp_path):
    p = tmp_path / "tcp.ndjson"
    fields = {f"topic_{i:04d}": 1234.5 for i in range(1500)}  # ~30 KB line
    p.write_text(json.dumps({"ts": "2026-09-24T12:00:20+00:00", "fields": fields}) + "\n")
    assert last_line_time(p) == datetime(2026, 9, 24, 12, 0, 20, tzinfo=timezone.utc)


def test_report_distinguishes_unreadable_from_missing(tmp_path):
    d = tmp_path / "2026-09-24"
    d.mkdir()
    (d / "wire.log").write_text("garbage without a timestamp\n")
    text = "\n".join(report(tmp_path, NOW, SERVICES, free_bytes=20 * 10**9, clock_synced="yes"))
    assert "wire.log: exists but no readable timestamp" in text
    assert "tcp.ndjson: no file yet for 2026-09-24" in text


def test_captures_dir_honours_env_and_defaults_to_home(tmp_path):
    from tools.givcap_status import captures_dir
    assert captures_dir({"CAPTURES_DIR": str(tmp_path)}) == tmp_path
    assert captures_dir({}) == Path.home() / "captures"
