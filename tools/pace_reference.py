"""PACE / Pylontech v2.5 protocol field and bit reference data.

Sources:
    - PACE v2.5 protocol spec (public copies in pylon community archives)
    - PbmsTools open-source viewer
    - libpace reference implementation
    - pylontech-rs Rust crate

This module is reference data only - no I/O, no parsing logic. Used by analysis
notebooks to test PACE-hypothesis-first against decoded GivEnergy BMS fields.

Cross-reference with giv-bms-analysis/docs/09-pace-comparison.md.
"""

# CID2 command codes (PACE Get* responses)
CID2_GET_ANALOG = 0x42      # cell voltages, temperatures, pack V/I, capacity
CID2_GET_ALARM = 0x44       # per-cell + pack-level alarm flags
CID2_GET_PROTECT = 0x47     # charge/discharge management info (limits, thresholds)
CID2_GET_MFR_INFO = 0x46    # manufacturer name, software/hardware version


# CID2=0x44 GetAlarmInfo pack-level alarm byte - bit positions.
# Hypothesis source for GivEnergy HR reg 19 (8-bit composite status).
PACK_ALARM_BITS = {
    0: "cell_overvoltage",
    1: "cell_undervoltage",
    2: "pack_overvoltage",
    3: "pack_undervoltage",
    4: "charge_overcurrent",
    5: "discharge_overcurrent",
    6: "charge_overtemp",
    7: "discharge_overtemp",
}


# CID2=0x44 GetAlarmInfo also exposes status bytes (separate from alarms):
#   - charge MOSFET status, discharge MOSFET status, balancing active, etc.
# Bit positions are typically:
PACK_STATUS_BITS = {
    0: "charge_mosfet_on",
    1: "discharge_mosfet_on",
    2: "charging",
    3: "discharging",
    4: "balancing_active",
    5: "heater_active",
    6: "fan_active",
    7: "reserved",
}


# CID2=0x47 ChargeDischargeManagementInfo - field names per the PACE spec.
# Used to test the hypothesis that Block 3 bytes 32-35 are protection thresholds.
PROTECTION_FIELD_NAMES = (
    "charge_voltage_limit",         # mV (per-cell or pack depending on vendor)
    "discharge_voltage_limit",
    "charge_current_limit",         # 0.1 A
    "discharge_current_limit",
    "cell_overvoltage_protect",     # mV per cell
    "cell_overvoltage_recover",
    "cell_undervoltage_protect",
    "cell_undervoltage_recover",
    "pack_overvoltage_protect",
    "pack_overvoltage_recover",
    "pack_undervoltage_protect",
    "pack_undervoltage_recover",
    "charge_overtemp_protect",      # 0.1 K
    "charge_overtemp_recover",
    "discharge_overtemp_protect",
    "discharge_overtemp_recover",
)


# Frame structure constants for PACE ASCII frames (used only as documentation;
# this module does NOT decode PACE frames).
PACE_FRAME_SOI = ord("~")           # 0x7E
PACE_FRAME_EOI = ord("\r")          # 0x0D
PACE_VER = 0x25                      # protocol version
PACE_CID1_LIFEPO4 = 0x46             # device-type CID1 for LFP packs
