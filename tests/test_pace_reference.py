"""Tests for the PACE reference data — verify constants match published PACE v2.5 spec."""
from tools.pace_reference import (
    CID2_GET_ANALOG, CID2_GET_ALARM, CID2_GET_PROTECT, CID2_GET_MFR_INFO,
    PACK_ALARM_BITS, PROTECTION_FIELD_NAMES,
)


def test_cid2_command_codes():
    assert CID2_GET_ANALOG == 0x42
    assert CID2_GET_ALARM == 0x44
    assert CID2_GET_PROTECT == 0x47
    assert CID2_GET_MFR_INFO == 0x46


def test_pack_alarm_bits_have_documented_positions():
    # PACE GetAlarmInfo pack-level alarm byte (one of several alarm bytes).
    # Bit positions per PbmsTools / libpace alarm-byte layout.
    assert PACK_ALARM_BITS[0] == "cell_overvoltage"
    assert PACK_ALARM_BITS[1] == "cell_undervoltage"
    assert PACK_ALARM_BITS[2] == "pack_overvoltage"
    assert PACK_ALARM_BITS[3] == "pack_undervoltage"
    assert PACK_ALARM_BITS[4] == "charge_overcurrent"
    assert PACK_ALARM_BITS[5] == "discharge_overcurrent"
    assert PACK_ALARM_BITS[6] == "charge_overtemp"
    assert PACK_ALARM_BITS[7] == "discharge_overtemp"


def test_protection_field_names_exist():
    # CID2=0x47 ChargeDischargeManagementInfo includes these named protection thresholds.
    for name in (
        "charge_voltage_limit",
        "discharge_voltage_limit",
        "charge_current_limit",
        "discharge_current_limit",
    ):
        assert name in PROTECTION_FIELD_NAMES
