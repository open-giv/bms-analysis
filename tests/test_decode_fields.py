"""Tests for decode_fields -- verify decoded values fall in plausible ranges
against captured-from-real-hardware sample frames.
"""
from pathlib import Path

import pytest

from tools.decode_fields import (
    decode_hr_response,
    decode_ir_block1,
    decode_ir_block2,
    decode_ir_block3,
)

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "sample_hr_response.bin").exists(),
    reason="fixtures not yet generated (see plan Task 6 step 1)",
)


def test_decode_hr_response_returns_known_field_set():
    data = (FIXTURES / "sample_hr_response.bin").read_bytes()
    fields = decode_hr_response(data)
    expected = {
        "hr11_soc_x100", "hr17_dynamic", "hr19_status",
        "hr23_pack_current_dA", "hr25_current_limit",
    }
    assert expected.issubset(fields.keys())


def test_decode_hr_response_pack_current_in_plausible_range():
    data = (FIXTURES / "sample_hr_response.bin").read_bytes()
    fields = decode_hr_response(data)
    assert -60000 <= fields["hr23_pack_current_dA"] <= 60000


def test_decode_hr_response_status_byte_fits_8_bits():
    data = (FIXTURES / "sample_hr_response.bin").read_bytes()
    fields = decode_hr_response(data)
    assert 0 <= fields["hr19_status"] <= 0xFF


def test_decode_ir_block1_extracts_serial_string():
    data = (FIXTURES / "sample_ir_block1.bin").read_bytes()
    fields = decode_ir_block1(data)
    serial = fields["serial"]
    assert isinstance(serial, str)
    assert len(serial) == 20


def test_decode_ir_block1_temperatures_in_plausible_range():
    data = (FIXTURES / "sample_ir_block1.bin").read_bytes()
    fields = decode_ir_block1(data)
    for i in range(5):
        t = fields[f"temp_{i}_decidegC"]
        assert -300 <= t <= 700, f"temp_{i} out of range: {t}"


def test_decode_ir_block2_cell_count_is_16():
    data = (FIXTURES / "sample_ir_block2.bin").read_bytes()
    fields = decode_ir_block2(data)
    assert fields["cell_count"] == 16


def test_decode_ir_block2_pack_voltage_in_plausible_range():
    data = (FIXTURES / "sample_ir_block2.bin").read_bytes()
    fields = decode_ir_block2(data)
    assert 40000 <= fields["pack_voltage_mV"] <= 60000


def test_decode_ir_block2_soc_pct_in_range():
    data = (FIXTURES / "sample_ir_block2.bin").read_bytes()
    fields = decode_ir_block2(data)
    assert 0 <= fields["soc_pct"] <= 100


def test_decode_ir_block3_returns_16_cell_voltages():
    data = (FIXTURES / "sample_ir_block3.bin").read_bytes()
    fields = decode_ir_block3(data)
    cells = [fields[f"cell_{i}_mV"] for i in range(16)]
    for i, v in enumerate(cells):
        assert 2200 < v < 3700, f"cell_{i} out of range: {v} mV"


def test_decode_ir_block3_min_max_cells_match_extremes():
    data = (FIXTURES / "sample_ir_block3.bin").read_bytes()
    fields = decode_ir_block3(data)
    cells = [fields[f"cell_{i}_mV"] for i in range(16)]
    assert fields["max_cell_mV"] == max(cells)
    assert fields["min_cell_mV"] == min(cells)
