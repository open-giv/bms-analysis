"""The analysis tools read captures that givcap-compress has gzipped, and redact.py redacts them.

The capture box gzips each finished UTC day (wire.log -> wire.log.gz), so every tool that reads a
capture has to take either form. redact.py used to treat a .gz as opaque bytes and search the
compressed data for serials, which never matches, so it wrote out an unredacted copy.
"""
import gzip
import json
import shutil

import pytest

from tools import extract_fields, parse_log
from tools.join_streams import load_tag_records, load_tcp_records, load_wire_records
from tools.redact import redact_file

WIRE = (
    "2026-09-24 12:00:00.000Z  00000000  01 03 00 00 00 1C 44 03                          |......D.|\n"
)
TCP = json.dumps({"ts": "2026-09-24T12:00:00+00:00", "fields": {"soc": 50}}) + "\n"
TAGS = json.dumps({"ts": "2026-09-24T12:00:00+00:00", "tag": "idle", "source": "manual"}) + "\n"
SERIAL = "AB1234C567"


def _gz(path):
    """Gzip path the way givcap-compress does, leaving only path.gz."""
    gz = path.with_name(path.name + ".gz")
    with open(path, "rb") as src, gzip.open(gz, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return gz


@pytest.mark.parametrize("module", [parse_log, extract_fields], ids=["parse_log", "extract_fields"])
def test_load_byte_stream_reads_gz_like_plain(tmp_path, module):
    plain = tmp_path / "plain" / "wire.log"
    plain.parent.mkdir()
    plain.write_text(WIRE)
    zipped = tmp_path / "zipped" / "wire.log"
    zipped.parent.mkdir()
    zipped.write_text(WIRE)
    assert module.load_byte_stream(_gz(zipped)) == module.load_byte_stream(plain)


def test_load_wire_records_reads_gz(tmp_path):
    path = tmp_path / "wire.log"
    path.write_text(WIRE)
    plain = load_wire_records(path)
    assert load_wire_records(_gz(path)).equals(plain)


def test_load_tcp_records_reads_gz(tmp_path):
    path = tmp_path / "tcp.ndjson"
    path.write_text(TCP)
    assert load_tcp_records(_gz(path))["tcp_soc"].tolist() == [50]


def test_load_tag_records_reads_gz(tmp_path):
    path = tmp_path / "tags.ndjson"
    path.write_text(TAGS)
    assert load_tag_records(_gz(path))["tag"].tolist() == ["idle"]


@pytest.mark.parametrize("name, content", [("wire.log", f"serial {SERIAL}\n"),
                                           ("tcp.ndjson", json.dumps({"fields": {"sn": SERIAL}}) + "\n")])
def test_redact_file_redacts_inside_gz(tmp_path, name, content):
    src = tmp_path / name
    src.write_text(content)
    gz = _gz(src)
    out = tmp_path / ("redacted-" + gz.name)
    redact_file(gz, out, {"serials": [SERIAL]})
    text = gzip.decompress(out.read_bytes()).decode()
    assert SERIAL not in text
    assert "X" * len(SERIAL) in text


def test_redacted_gz_with_default_name_still_reads(tmp_path):
    # redact.py's default output for wire.log.gz is wire.log.gz.redacted, which no longer ends in .gz.
    path = tmp_path / "wire.log"
    path.write_text(WIRE)
    gz = _gz(path)
    out = tmp_path / "wire.log.gz.redacted"
    redact_file(gz, out, {"serials": [SERIAL]})
    assert parse_log.load_byte_stream(out) == parse_log.load_byte_stream(gz)
    assert extract_fields.load_byte_stream(out) == parse_log.load_byte_stream(gz)


def test_redact_file_handles_redacted_gz_name(tmp_path):
    src = tmp_path / "tcp.ndjson"
    src.write_text(json.dumps({"fields": {"sn": SERIAL}}) + "\n")
    gz = _gz(src)
    renamed = tmp_path / "tcp.ndjson.gz.redacted"
    gz.rename(renamed)
    out = tmp_path / "again"
    redact_file(renamed, out, {"serials": [SERIAL]})
    assert SERIAL not in gzip.decompress(out.read_bytes()).decode()
