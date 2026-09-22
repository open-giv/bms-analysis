"""givcap-compress gzips finished days and leaves today alone."""
import gzip
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "capture-box" / "givcap-compress"


def _run(captures, today):
    subprocess.run(["bash", str(SCRIPT)], check=True,
                   env={"CAPTURES_DIR": str(captures), "TODAY": today, "PATH": "/usr/bin:/bin"})


def _day(captures, day, content="line\n"):
    d = captures / day
    d.mkdir(parents=True)
    (d / "wire.log").write_text(content)
    (d / "tcp.ndjson").write_text(content)
    return d


def test_compress_gzips_past_days_only(tmp_path):
    old = _day(tmp_path, "2026-09-23")
    today = _day(tmp_path, "2026-09-24")
    _run(tmp_path, "2026-09-24")
    assert sorted(p.name for p in old.iterdir()) == ["tcp.ndjson.gz", "wire.log.gz"]
    assert gzip.decompress((old / "wire.log.gz").read_bytes()) == b"line\n"
    assert sorted(p.name for p in today.iterdir()) == ["tcp.ndjson", "wire.log"]


def test_compress_is_idempotent(tmp_path):
    old = _day(tmp_path, "2026-09-23")
    _run(tmp_path, "2026-09-24")
    _run(tmp_path, "2026-09-24")
    assert sorted(p.name for p in old.iterdir()) == ["tcp.ndjson.gz", "wire.log.gz"]


def test_compress_overwrites_partial_gz(tmp_path):
    old = _day(tmp_path, "2026-09-23", content="full day\n")
    (old / "wire.log.gz").write_bytes(b"\x1f\x8b truncated")  # left by a power cut
    _run(tmp_path, "2026-09-24")
    assert gzip.decompress((old / "wire.log.gz").read_bytes()) == b"full day\n"
    assert not (old / "wire.log").exists()


def test_compress_ignores_non_date_folders(tmp_path):
    other = tmp_path / "notes"
    other.mkdir()
    (other / "readme.txt").write_text("keep\n")
    _run(tmp_path, "2026-09-24")
    assert (other / "readme.txt").exists()


def test_compress_handles_empty_captures_dir(tmp_path):
    _run(tmp_path, "2026-09-24")


def test_compress_syncs_gz_to_disk_before_removing_source(tmp_path):
    captures = tmp_path / "captures"
    old = _day(captures, "2026-09-23")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    marker = tmp_path / "sources_at_sync.txt"
    (fakebin / "sync").write_text(f"#!/bin/bash\nls {old} >> {marker}\n")
    (fakebin / "sync").chmod(0o755)
    subprocess.run(["bash", str(SCRIPT)], check=True,
                   env={"CAPTURES_DIR": str(captures), "TODAY": "2026-09-24",
                        "PATH": f"{fakebin}:/usr/bin:/bin"})
    at_sync = marker.read_text().split()
    assert "wire.log" in at_sync and "wire.log.gz" in at_sync  # both present when sync ran
    assert sorted(p.name for p in old.iterdir()) == ["tcp.ndjson.gz", "wire.log.gz"]
