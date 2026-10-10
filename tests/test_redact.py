"""Tests for redact.py -- verify ASCII serial, hex-encoded serial, and IP redaction."""
import pytest

from tools.redact import redact_text, redact_bytes, load_config_dict


def test_redact_text_replaces_ascii_serial():
    cfg = {"serials": ["DX2319G279"]}
    out = redact_text("inverter DX2319G279 reports OK", cfg)
    assert "DX2319G279" not in out
    assert "X" * 10 in out


def test_redact_text_replaces_ip():
    cfg = {"ips": ["192.168.1.42"]}
    out = redact_text("connecting to 192.168.1.42:8899", cfg)
    assert "192.168.1.42" not in out
    assert "X.X.X.X" in out


def test_redact_text_idempotent():
    cfg = {"serials": ["ABC"]}
    once = redact_text("hi ABC bye", cfg)
    twice = redact_text(once, cfg)
    assert once == twice


def test_redact_bytes_replaces_serial_in_byte_payload():
    payload = b"\x01\x02ABC\x03\x04"
    cfg = {"serials": ["ABC"]}
    out = redact_bytes(payload, cfg)
    assert b"ABC" not in out
    assert out == b"\x01\x02XXX\x03\x04"


def test_redact_text_handles_empty_config():
    cfg = {}
    text = "nothing to redact"
    assert redact_text(text, cfg) == text


def test_load_config_dict_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GIVE_REDACT_SERIALS", "AAA,BBB")
    monkeypatch.setenv("GIVE_REDACT_IPS", "10.0.0.1")
    # Pass nonexistent path so file lookup falls through to env
    cfg = load_config_dict(tmp_path / "nonexistent.toml")
    assert "AAA" in cfg["serials"]
    assert "BBB" in cfg["serials"]
    assert "10.0.0.1" in cfg["ips"]


# --- parquet ---------------------------------------------------------------------------------

import pyarrow as pa
import pyarrow.parquet as pq

from tools.redact import redact_file, main

SERIAL = "DX2319G279"
CFG = {"serials": [SERIAL], "ips": ["192.168.1.42"]}


def _capture_table():
    """A joined capture as the tools write it: decoded serial, raw frame hex, raw bytes, numbers."""
    return pa.table(
        {
            "ts": pa.array([1, 2, 3], pa.int64()),
            "serial": pa.array([f"{SERIAL}          ", None, f"{SERIAL}          "]),
            "raw_hex": pa.array(["01 04 " + " ".join(f"{ord(c):02x}" for c in SERIAL), "01 03", None]),
            "frame": pa.array([b"\x01\x04" + SERIAL.encode(), b"\x01\x03", None], pa.binary()),
            "host": pa.array(["192.168.1.42", "x", "y"]).dictionary_encode(),
            "hr22_pack_voltage_cV": pa.array([5488, 5490, 5491], pa.int32()),
        }
    )


@pytest.mark.parametrize("compression", ["snappy", "zstd", "gzip", "none"])
def test_redact_parquet_removes_serial_from_every_column(tmp_path, compression):
    src, out = tmp_path / "c.joined.parquet", tmp_path / "c.joined.parquet.redacted"
    pq.write_table(_capture_table(), src, compression=compression)
    count = redact_file(src, out, CFG)
    data = out.read_bytes()
    assert SERIAL.encode() not in data and b"192.168.1.42" not in data
    t = pq.read_table(out)
    assert t.schema == _capture_table().schema
    assert t.column("serial").to_pylist() == ["XXXXXXXXXX          ", None, "XXXXXXXXXX          "]
    assert SERIAL not in t.column("raw_hex")[0].as_py()
    assert t.column("frame")[0].as_py() == b"\x01\x04XXXXXXXXXX"
    assert t.column("host").cast(pa.string()).to_pylist() == ["X.X.X.X", "x", "y"]
    assert t.column("hr22_pack_voltage_cV").to_pylist() == [5488, 5490, 5491]
    assert count == 5  # two decoded serials, one hex serial, one raw-byte serial, one IP


def test_redact_parquet_is_idempotent(tmp_path):
    src, once, twice = tmp_path / "a.parquet", tmp_path / "b.parquet", tmp_path / "c.parquet"
    pq.write_table(_capture_table(), src)
    redact_file(src, once, CFG)
    assert redact_file(once, twice, CFG) == 0
    assert pq.read_table(once).equals(pq.read_table(twice))


def test_parquet_found_by_magic_not_name(tmp_path):
    src, out = tmp_path / "capture.redacted", tmp_path / "out"
    pq.write_table(_capture_table(), src)
    redact_file(src, out, CFG)
    assert SERIAL not in pq.read_table(out).column("serial")[0].as_py()


def test_text_count_counts_digit_only_hex_once(tmp_path):
    # "12" in hex is "31 32" in either case, so it must not be counted twice.
    src, out = tmp_path / "w.log", tmp_path / "w.out"
    src.write_text("frame 31 32 end")
    assert redact_file(src, out, {"serials": ["12"]}) == 1


def test_main_refuses_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("GIVE_REDACT_SERIALS", raising=False)
    monkeypatch.delenv("GIVE_REDACT_IPS", raising=False)
    src = tmp_path / "w.log"
    src.write_text("anything")
    monkeypatch.setattr("sys.argv", ["redact.py", str(src), "--config", str(tmp_path / "none.toml")])
    with pytest.raises(SystemExit) as e:
        main()
    assert "No serials or IPs configured" in str(e.value)
    assert not (tmp_path / "w.log.redacted").exists()
