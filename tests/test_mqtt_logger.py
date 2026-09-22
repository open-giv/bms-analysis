"""Unit tests for mqtt_logger: naming, payloads, snapshots, config."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tools import mqtt_logger
from tools.join_streams import load_tcp_records
from tools.mqtt_logger import SnapshotWriter, field_name, load_config, parse_payload

PREFIX = "GivEnergy/XXXXXXXXXX"
T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_field_name_uses_poller_name_for_mapped_topic(monkeypatch):
    monkeypatch.setitem(mqtt_logger.TOPIC_TO_FIELD, "Battery_Details/SOC", "soc")
    assert field_name(f"{PREFIX}/Battery_Details/SOC", PREFIX) == "soc"


def test_field_name_joins_unmapped_topic_path_with_underscores():
    assert field_name(f"{PREFIX}/Power/Power/Battery_Power", PREFIX) == "Power_Power_Battery_Power"


def test_field_name_keeps_whole_topic_when_prefix_differs():
    assert field_name("other/Thing-One", PREFIX) == "other_Thing_One"


def test_field_name_keeps_serial_case_so_redact_matches():
    # redact.py replaces serials case-sensitively; lower-casing would hide them from it.
    assert field_name(f"{PREFIX}/Battery_Details/BG1234G567/SOC", PREFIX) == "Battery_Details_BG1234G567_SOC"


def test_parse_payload_numbers_and_text():
    assert parse_payload(b"42") == 42
    assert parse_payload(b" 51.2 ") == 51.2
    assert parse_payload(b"Normal") == "Normal"
    assert parse_payload(b'{"a": 1}') == '{"a": 1}'


def test_parse_payload_empty_and_non_finite_are_null():
    assert parse_payload(b"") is None
    assert parse_payload(b"  ") is None
    assert parse_payload(b"nan") is None
    assert parse_payload(b"inf") is None
    assert parse_payload(b"-Infinity") is None


def test_writer_writes_full_snapshot(tmp_path):
    w = SnapshotWriter(str(tmp_path / "%Y-%m-%d" / "tcp.ndjson"))
    w.update("soc", 50)
    w.update("v_battery", 52.1)
    assert w.maybe_write(T0) is True
    recs = _records(tmp_path / "2026-09-24" / "tcp.ndjson")
    assert recs == [{"ts": "2026-09-24T12:00:00+00:00", "fields": {"soc": 50, "v_battery": 52.1}}]


def test_writer_skips_when_nothing_changed(tmp_path):
    w = SnapshotWriter(str(tmp_path / "tcp.ndjson"))
    w.update("soc", 50)
    assert w.maybe_write(T0) is True
    w.update("soc", 50)
    assert w.maybe_write(T0 + timedelta(seconds=5)) is False


def test_writer_writes_at_most_once_per_interval(tmp_path):
    w = SnapshotWriter(str(tmp_path / "tcp.ndjson"))
    w.update("soc", 50)
    assert w.maybe_write(T0) is True
    w.update("soc", 51)
    assert w.maybe_write(T0 + timedelta(seconds=0.5)) is False
    assert w.maybe_write(T0 + timedelta(seconds=1.0)) is True
    assert [r["fields"]["soc"] for r in _records(tmp_path / "tcp.ndjson")] == [50, 51]


def test_writer_switches_file_at_utc_midnight_with_full_snapshot(tmp_path):
    w = SnapshotWriter(str(tmp_path / "%Y-%m-%d" / "tcp.ndjson"))
    late = datetime(2026, 9, 24, 23, 59, 59, tzinfo=timezone.utc)
    w.update("soc", 50)
    w.update("v_battery", 52.1)
    assert w.maybe_write(late) is True
    w.update("soc", 49)
    assert w.maybe_write(late + timedelta(seconds=2)) is True
    day2 = _records(tmp_path / "2026-09-25" / "tcp.ndjson")
    assert day2[0]["fields"] == {"soc": 49, "v_battery": 52.1}


def test_writer_output_is_strict_json(tmp_path):
    w = SnapshotWriter(str(tmp_path / "tcp.ndjson"))
    w.update("odd", parse_payload(b"nan"))
    w.maybe_write(T0)
    line = (tmp_path / "tcp.ndjson").read_text().strip()
    json.loads(line, parse_constant=lambda c: pytest.fail(f"non-standard JSON constant {c}"))


def test_writer_rejects_naive_time(tmp_path):
    w = SnapshotWriter(str(tmp_path / "tcp.ndjson"))
    w.update("soc", 50)
    with pytest.raises(ValueError):
        w.maybe_write(datetime(2026, 9, 24, 12, 0, 0))


def test_join_streams_reads_writer_output_as_utc(tmp_path):
    w = SnapshotWriter(str(tmp_path / "tcp.ndjson"))
    w.update("soc", 50)
    w.maybe_write(T0)
    df = load_tcp_records(tmp_path / "tcp.ndjson")
    assert df["tcp_soc"].tolist() == [50]
    assert str(df["ts"].dt.tz) == "UTC"


def test_load_config_reads_env_with_defaults():
    cfg = load_config({"MQTT_HOST": "ha.local", "MQTT_USER": "givcap",
                       "MQTT_PASSWORD": "pw", "MQTT_TOPIC_PREFIX": "GivEnergy/"})
    assert cfg == {"host": "ha.local", "port": 1883, "user": "givcap", "password": "pw",
                   "prefix": "GivEnergy", "path": "~/captures/%Y-%m-%d/tcp.ndjson"}


def test_load_config_names_every_missing_variable():
    with pytest.raises(SystemExit) as exc:
        load_config({"MQTT_HOST": "ha.local"})
    msg = str(exc.value)
    for name in ("MQTT_USER", "MQTT_PASSWORD", "MQTT_TOPIC_PREFIX"):
        assert name in msg


def test_join_streams_survives_mixed_type_tcp_column(tmp_path):
    import pandas as pd
    from tools.join_streams import join_streams
    tcp_path = tmp_path / "tcp.ndjson"
    lines = [{"ts": "2026-09-24T12:00:00+00:00", "fields": {"soc": 50, "mode": "Eco"}},
             {"ts": "2026-09-24T12:00:01+00:00", "fields": {"soc": "unknown", "mode": "Eco"}},
             {"ts": "2026-09-24T12:00:02+00:00", "fields": {"soc": 51, "mode": "Timed"}}]
    tcp_path.write_text("".join(json.dumps(r) + "\n" for r in lines))
    tcp = load_tcp_records(tcp_path)
    assert tcp["tcp_soc"].tolist()[0] == 50 and pd.isna(tcp["tcp_soc"].tolist()[1])
    assert tcp["tcp_mode"].tolist() == ["Eco", "Eco", "Timed"]
    wire = pd.DataFrame([{"ts": pd.Timestamp("2026-09-24 12:00:01.5", tz="UTC"), "fc": 3}])
    join_streams(wire, tcp, pd.DataFrame()).to_parquet(tmp_path / "joined.parquet")
