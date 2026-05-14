from datetime import datetime
from io import StringIO
import csv

from tools.extract_fields import parse_register_filter, write_register_state_csv


def _make_fc3_pair(device, start_register, values, ts):
    register_count = len(values)
    req_raw = bytes([
        device,
        3,
        (start_register >> 8) & 0xFF,
        start_register & 0xFF,
        (register_count >> 8) & 0xFF,
        register_count & 0xFF,
        0,
        0,
    ])
    rsp_raw = bytearray([device, 3, register_count * 2])
    for value in values:
        rsp_raw.append((value >> 8) & 0xFF)
        rsp_raw.append(value & 0xFF)
    rsp_raw.extend([0, 0])

    req = {
        "ts": ts,
        "device": device,
        "fc": 3,
        "raw": req_raw,
        "role": "request",
    }
    rsp = {
        "ts": ts,
        "device": device,
        "fc": 3,
        "raw": bytes(rsp_raw),
        "role": "response",
    }
    return req, rsp


def test_parse_register_filter_accepts_byte_offset():
    parsed = parse_register_filter(["w@1:3:0x28+1"])
    assert parsed == [("w", (1, 3, 0x28, 1))]


def test_write_register_state_csv_supports_split_word_offset():
    req, rsp = _make_fc3_pair(1, 0x28, [0x1122, 0x3344], datetime(2026, 5, 14, 12, 0, 0))
    out = StringIO()

    write_register_state_csv(
        [(req, rsp)],
        out,
        register_filter=parse_register_filter(["w@1:3:0x28+1"]),
    )

    rows = list(csv.reader(StringIO(out.getvalue())))
    assert rows[0] == ["timestamp", "device_001_fc_03_reg_00040_byte_001"]
    assert rows[1][1] == str(0x2233)
