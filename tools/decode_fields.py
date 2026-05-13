"""Field-level decoder for GivEnergy BMS Modbus response frames.

Takes the data portion of a parsed frame (no device/fc/CRC/length headers) and
returns a flat dict of named fields.

Field mappings follow:
    docs/02-holding-registers.md  (FC=3 HR(0..27) responses)
    docs/03-input-registers.md    (FC=4 IR Block 1/2/3 responses)

Each block has a fixed expected length; mismatched lengths return an empty dict
rather than raising.
"""
from typing import Any, Dict


def _u16_be(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def _s16_be(data: bytes, offset: int) -> int:
    v = _u16_be(data, offset)
    return v - 0x10000 if v >= 0x8000 else v


def decode_hr_response(data: bytes) -> Dict[str, Any]:
    """Decode HR(0..27) -> 56-byte response data portion.

    Byte offsets are per docs/02-holding-registers.md (reg * 2 = byte offset):
      Reg 11 -> bytes 22-23: SoC * 100 (uint16)
      Reg 17 -> bytes 34-35: dynamic hash (uint16)
      Reg 19 -> bytes 38-39: composite status/fault bitmask (observed values
                              fit in 8 bits; read as uint16 per register model)
      Reg 23 -> bytes 46-47: pack current in centi-amps (signed int16, 0.01 A)
      Reg 25 -> bytes 50-51: current limit * 100 (uint16)
    """
    if len(data) != 56:
        return {}
    fields: Dict[str, Any] = {
        "hr11_soc_x100": _u16_be(data, 22),
        "hr17_dynamic": _u16_be(data, 34),
        "hr19_status": _u16_be(data, 38),
        "hr23_pack_current_cA": _s16_be(data, 46),
        "hr25_current_limit": _u16_be(data, 50),
    }
    return fields


def decode_ir_block1(data: bytes) -> Dict[str, Any]:
    """Decode IR Block 1 (FC=4 start=0x0000 count=21) -> 42-byte data portion.

    Byte offsets are per docs/03-input-registers.md Block 1 layout:
      Bytes  0-19: ASCII serial number (20 bytes, space-padded)
      Byte  20-21: unknown / 0x0000
      Bytes 22-31: 5 temperature sensors, 2 bytes each, signed int16 decidegC
                   (firmware removes the +2730 bias before TX; wire bytes are
                   raw decidegC with no decode transform needed)
    """
    if len(data) != 42:
        return {}
    fields: Dict[str, Any] = {}
    fields["serial"] = data[0:20].decode("ascii", errors="replace")
    for i in range(5):
        fields[f"temp_{i}_decidegC"] = _s16_be(data, 22 + i * 2)
    return fields


def decode_ir_block2(data: bytes) -> Dict[str, Any]:
    """Decode IR Block 2 (FC=4 start=0x0015 count=19) -> 38-byte data portion.

    Byte offsets are per docs/03-input-registers.md Block 2 layout:
      Byte   0:    Number of cells (e.g. 0x10 = 16)
      Bytes  1-2:  Cycle count (uint16)
      Bytes  7-8:  Pack voltage, 0.001 V scale = mV (uint16; e.g. 0xCF85 = 53125 mV)
      Bytes 15-16: Total (calibrated) capacity in 0.01 Ah units / centi-Ah (uint16)
      Bytes 19-20: Design capacity in 0.01 Ah units / centi-Ah (uint16)
      Bytes 23-24: Remaining capacity in 0.01 Ah units / centi-Ah (uint16)
      Byte  25:    State of Charge in percent (0-100)
      Bytes 35-36: BMS firmware version (uint16; e.g. 0x0BCE = 3022)
    """
    if len(data) != 38:
        return {}
    fields: Dict[str, Any] = {
        "cell_count": data[0],
        "cycle_count": _u16_be(data, 1),
        "pack_voltage_mV": _u16_be(data, 7),
        "total_cap_cAh": _u16_be(data, 15),
        "design_cap_cAh": _u16_be(data, 19),
        "remain_cap_cAh": _u16_be(data, 23),
        "soc_pct": data[25],
        "fw_version": _u16_be(data, 35),
    }
    return fields


def decode_ir_block3(data: bytes) -> Dict[str, Any]:
    """Decode IR Block 3 (FC=4 start=0x0028 count=20) -> 40-byte data portion.

    Byte offsets are per docs/03-input-registers.md Block 3 layout:
      Bytes  0-31: 16 cell voltages, 2 bytes each, raw mV big-endian (no offset)
      Bytes 32-33: unknown field, (value - 2730) encoded; add 2730 to decode
      Bytes 34-35: unknown field, (value - 2730) encoded; add 2730 to decode
      Bytes 36-37: Max cell voltage, raw mV
      Bytes 38-39: Min cell voltage, raw mV
    """
    if len(data) != 40:
        return {}
    fields: Dict[str, Any] = {}
    for i in range(16):
        fields[f"cell_{i}_mV"] = _u16_be(data, i * 2)
    fields["block3_b32_offset"] = _s16_be(data, 32) + 2730
    fields["block3_b34_offset"] = _s16_be(data, 34) + 2730
    fields["max_cell_mV"] = _u16_be(data, 36)
    fields["min_cell_mV"] = _u16_be(data, 38)
    return fields


def decode_response(rsp_frame, req_frame=None) -> Dict[str, Any]:
    """Dispatch on frame type and return decoded fields.

    `rsp_frame` is a parse_log frame dict (has `.raw`, `.fc`, etc).
    `req_frame` (optional) provides the address/count for FC=4 dispatch.
    """
    fc = rsp_frame["fc"]
    raw = rsp_frame["raw"]
    if fc == 3:
        return decode_hr_response(bytes(raw[3:-2]))
    if fc == 4 and req_frame is not None:
        addr = (req_frame["raw"][2] << 8) | req_frame["raw"][3]
        data = bytes(raw[4:-2])
        if addr == 0x0000:
            return decode_ir_block1(data)
        if addr == 0x0015:
            return decode_ir_block2(data)
        if addr == 0x0028:
            return decode_ir_block3(data)
    return {}
