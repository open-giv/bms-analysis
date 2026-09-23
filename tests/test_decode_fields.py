"""Tests for decode_fields -- verify decoded values fall in plausible ranges
against captured-from-real-hardware sample frames.
"""
from pathlib import Path

import pytest

from tools.decode_fields import (
    HR19_BITS,
    HR19_EVIDENCE_LEVELS,
    decode_hr19,
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
        "hr11_capacity_Ah", "hr17_dynamic", "hr19_status",
        "hr23_pack_current_cA", "hr25_current_limit",
    }
    assert expected.issubset(fields.keys())


def test_decode_hr_response_pack_current_in_plausible_range():
    data = (FIXTURES / "sample_hr_response.bin").read_bytes()
    fields = decode_hr_response(data)
    assert -60000 <= fields["hr23_pack_current_cA"] <= 60000


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


def test_decode_hr_response_hr11_is_capacity_in_whole_ah():
    data = (FIXTURES / "sample_hr_response.bin").read_bytes()
    fields = decode_hr_response(data)
    assert fields["hr11_capacity_Ah"] == 186  # one 9.5 kWh pack
    assert "hr11_soc_x100" not in fields


def test_decode_ir_block3_max_min_temps_are_decidegC():
    data = (FIXTURES / "sample_ir_block3.bin").read_bytes()
    fields = decode_ir_block3(data)
    assert fields["max_temp_decidegC"] == 179  # 00 B3 = 17.9 degC
    assert fields["min_temp_decidegC"] == 165  # 00 A5 = 16.5 degC
    assert "block3_b32_offset" not in fields
    assert "block3_b34_offset" not in fields


def test_decode_ir_block3_temps_below_zero_are_negative():
    data = bytearray((FIXTURES / "sample_ir_block3.bin").read_bytes())
    data[32:36] = bytes.fromhex("FF9C FFCE")  # -10.0 degC, -5.0 degC
    fields = decode_ir_block3(bytes(data))
    assert fields["max_temp_decidegC"] == -100
    assert fields["min_temp_decidegC"] == -50


def test_hr19_bits_cover_all_eight_bits_with_evidence_level():
    assert sorted(HR19_BITS) == list(range(8))
    for bit, (name, evidence) in HR19_BITS.items():
        assert name.isidentifier(), f"bit {bit}: {name!r}"
        assert evidence in HR19_EVIDENCE_LEVELS, f"bit {bit}: {evidence!r}"


def test_decode_hr19_discharging_with_cells_ok():
    flags = decode_hr19(207)  # 1100 1111, seen on every discharging poll in the G3 capture
    assert flags["discharging_or_idle"] is True
    assert flags["current_flowing"] is True
    assert flags["all_cells_ok"] is True
    assert flags["protection_active"] is False


def test_decode_hr19_charging():
    flags = decode_hr19(206)  # 1100 1110, seen on every charging poll
    assert flags["discharging_or_idle"] is False
    assert flags["current_flowing"] is True


def test_decode_hr19_at_rest_with_low_cell():
    flags = decode_hr19(198)  # 1100 0110, seen at the 4% floor
    assert flags["all_cells_ok"] is False


def test_decode_hr_response_remaining_registers():
    data = (FIXTURES / "sample_hr_response.bin").read_bytes()
    fields = decode_hr_response(data)
    assert fields["hr12_hw_rev"] == 48
    assert fields["hr13_bms_fw"] == 3022
    assert fields["hr14_charge_mode_active"] == 0
    assert fields["hr15_flags"] == 0
    assert fields["hr16_afe_state"] == 0
    assert fields["hr18_hash_hi"] == 14493
    assert fields["hr20_alarms"] == 0
    assert fields["hr21_soc_pct"] == 95
    assert fields["hr22_pack_voltage_cV"] == 5311      # 53.11 V
    assert fields["hr24_max_temp_C"] == 17
    # Neutral names: which of HR26/HR27 is the charge limit differs by inverter (docs/02 G3 LV note).
    assert fields["hr26_limit_cA"] == 8370             # 83.70 A
    assert fields["hr27_limit_cA"] == 8370


def test_decode_hr_response_max_temp_is_signed():
    data = bytearray((FIXTURES / "sample_hr_response.bin").read_bytes())
    data[48:50] = (-5 & 0xFFFF).to_bytes(2, "big")
    assert decode_hr_response(bytes(data))["hr24_max_temp_C"] == -5
