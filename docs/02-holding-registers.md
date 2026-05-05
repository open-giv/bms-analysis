# Holding registers (FC=3)

The inverter polls a single block of 28 holding registers (offsets `0x0000`-`0x001B`) from **device 1 only**, every ~245 ms. This is the highest-rate query on the bus and carries the BMS's real-time status.

## Poll request and response shape

```
Request:   01 03 00 00 00 1C 44 03                     (8 bytes; device=1, FC=3, start=0, count=28, CRC)
Response:  01 03 38 [56 data bytes] [crc_lo crc_hi]    (61 bytes; standard Modbus with byte_count=0x38)
```

FC=3 uses standard Modbus framing (with byte_count, unlike FC=4). See [01-protocol.md](01-protocol.md) for details.

## Cadence

| Metric | Value |
|---|---|
| Average gap between requests | 245 ms |
| Minimum gap | 231 ms |
| Maximum gap | 481 ms |
| BMS turnaround latency (req -> rsp) | ~101 ms (p95: 103 ms; range 90-114 ms) |
| Device addressed | 1 only (the primary battery; HR is never polled to other devices) |

The device-1 hard-coding is confirmed both empirically and from inverter firmware analysis: the FA-series Gen 3 builder writes `movs r0, #1; strb r0, [sp]` for the device byte unconditionally on the HR path.

## Register layout

The 28 registers (= 56 bytes) decoded at the byte level:

| Reg | Bytes | Empirical observation | Firmware-derived interpretation |
|----:|---|---|---|
| 0   | 0-1   | constant `0x0065` (101) | Init writes literal `0x65`. **Fixed protocol/device marker constant** (not the device address - that's set by dipswitches). |
| 1-4 | 2-9   | constant `0xFFFF` x 4 | Never written after the 0xFFFF init. **Truly unused / reserved.** |
| 5-9 | 10-19 | ASCII serial number (e.g. `XXXXXXXXXX`) | 5 halfwords copied big-endian from a 10-byte SRAM struct. |
| 10  | 20-21 | constant `0xFFFF` | Never written. **Unused.** |
| 11  | 22-23 | varies (`0x00BA -> 0x0174` once in capture) | **`(SoC_float * 100)`** - SoC encoded as 0.01 % units. `0x00BA` = 1.86 %, `0x0174` = 3.72 %. Computed via `0x801c2ac` (float-mul) + `0x801c468` (float-to-uint). Note the small magnitude - this may be a different "SoC-like" metric (e.g. balancing-cell SoC, not main pack SoC). |
| 12  | 24-25 | constant `0x0030` (48) | Init writes literal `0x30`. Possibly a **hardware revision** field. |
| 13  | 26-27 | constant `0x0BCE` (3022) | Confirmed: `movw r0, #0xbce; strh r0, [r4, #0x1a]`. **BMS firmware version.** |
| 14  | 28-29 | constant `0x0000` in capture | Set to 0 or 1 from a flag byte. **Boolean status** (not yet observed transitioning - needs labelled captures). |
| 15  | 30-31 | constant `0x0000` in capture | OR-mask of 3 conditional bits (`#1`, `#2`, `#4`). **3-flag composite status.** |
| 16  | 32-33 | constant `0x0000` in capture | Single byte loaded from RAM. **Mode/state byte.** |
| 17  | 34-35 | varies (`0x114A`-`0x1219`, ~142 distinct values) | **Hash low-half of 6 dynamic bytes from SRAM `0x20000105..0x2000010A`**, refreshed each cycle (~1/sec) by `0x08001362` then hashed at `0x0800_D584`. The bytes encode some live state (changes ~1/sec). |
| 18  | 36-37 | constant `0x389D` (14493) across all 829 captures | Hash high-half of the same 6-byte source. Constant per-device for this device, but technically derived from the same dynamic bytes - the high-half computation just happens to be invariant for this device's state range. |
| 19  | 38-39 | See [Register 19 Bits](#register-19-bits) | OR-mask of 8 specific bits: bit0/1 from sign of `*(int*)0x2000014C`; bit2 from `[0x20000140]`; bit3 from `[0x200000CE]`; bit4 from `[0x2000009D]`; bit5 conditional; bit6/7 from `[0x2000027B]` bits 5/6. **Composite status byte.** |
| 20  | 40-41 | See [Register 20 Bits](#register-20-bits) | a set of per-pack online flags (8-bit OR composite from `[0x20000279]` and per-pack walk) |
| 21  | 42-43 | 3 distinct values `0x005D`-`0x005F` | `*(u16)0x20000184`. Possibly main pack SoC % (93-95%) - decreased over capture, plausible. |
| 22  | 44-45 | Battery Voltage in units of 0.01V, measured at primary battery pack. |
| 23  | 46-47 | 85 distinct values across signed range | **Signed pack current in deciAmps (0.1 A units)**, NOT centi-amps. From `*(float*)0x2000014C * 10.0f` via float-to-signed-int. Positive values indicate charging, negative discharging. |
| 24  | 48-49 | nearly constant `0x0011` (17) | `(min_cell_mV - 2730) / 10` - encodes min cell voltage with `-2730` baseline, then divides by 10. So `0x0011` = 17 -> `17 * 10 + 2730 = 2900 mV` min cell. (For a 3.31 V/cell pack, this is unexpectedly low - might track the *floor* rather than current min, or a different sensor.) |
| 25  | 50-51 | constant `0x2328` (9000) in capture | **DYNAMIC**: `*(u16)0x20001598 * 100`. The observed `0x2328` reflects source halfword = 90 (i.e. 90.00 A continuous limit at the time). Configurable. |
| 26  | 52-53 | 10 distinct values, varies with 27 | `*(u16)0x20000142`. **Independent source** - they only happen to track each other in steady state. |
| 27  | 54-55 | 10 distinct values, varies with 26 | `*(u16)0x20000144`. Independent source. |

### Register 19 Bits

Register 19 seems to be a set of status bits, indicating the BMS status to the inverter.

| Bit | Meaning (if set) | Analysis |
|-----|------------------|----------|
| 1 (lsb) | Discharging | From protocol analysis and from sign of `*(int*)0x2000014C` |
| 2 ||Normally high|
| 3 |Request Charge?|Normally high, low for extended period of min SOC|
| 4 |Battery MOSFETs enabled?|Normally high, oscillates below 4% SOC and near 100% SOC during calibration|
| 5 ||Normally low|
| 6 |Forbid Charge?|Normally low, high briefly at max SOC during calibration|
| 7 |Allow Discharge?|Normally high, low at minimum SOC during calibration|
| 8 |Allow Charge and Discharge?|Appears related to bits 6 & 7 - normally high, low when bit 7 low or bit 6 high|
| 9 || Unused? |
| 10 || Unused? |
| 11 || Unused? |
| 12 || Unused? |
| 13 || Unused? |
| 14 || Unused? |
| 15 || Unused? |
| 16 (msb) || Unused? |

### Register 20 Bits

Register 20 is a set of alarm bits collated from all packs.  From the values seen, it appears to directly correspond to the bits labelled `BMS xxx` in GivTCP `battery_fault_code` (see [GivTCP register.py](https://github.com/britkat1980/giv_tcp/blob/b6a3ba85c5d81f0acaad0e574fccb902aa23b03c/GivTCP/givenergy_modbus_async/model/register.py#L216))

| Bit | Meaning (if set) | Analysis |
|-----|------------------|----------|
| 1 (lsb) | Over Current | From GivTCP, battery status enum |
| 2 | Short Current | From GivTCP, battery status enum |
| 3 | Over Voltage | From protocol analysis during calibration cycle, seen briefly at end of calibration (max SOC) and from GivTCP, battery status enum |
| 4 | Under Voltage | From protocol analysis during calibration cycle, seen at minimum SOC and from GivTCP, battery status enum |
| 5 | Discharge over temperature | From GivTCP, battery status enum |
| 6 | Charge over temperature | From GivTCP, battery status enum |
| 7 | Discharge under temperature | From GivTCP, battery status enum |
| 8 | Charge under temperature | From GivTCP, battery status enum |
| 9 || Unused? |
| 10 || Unused? |
| 11 || Unused? |
| 12 || Unused? |
| 13 || Unused? |
| 14 || Unused? |
| 15 || Unused? |
| 16 (msb) || Unused? |

## Field-variation analysis

Across 829 HR responses captured by Ken (over a 3.4-minute cold-start window):

| Type | Count |
|---|---:|
| Registers that are constant | 14 |
| Registers with 2-3 distinct values (state-like) | 4 |
| Registers with slow drift (5-10 distinct values) | 3 |
| Registers with fast variation (85+ distinct values) | 1 (current, reg 23) |

The capture happened with the system in approximately steady state (low current, idle/charging at low rate), which is why most fields didn't transition. **Captures under varied load conditions are needed to lock down the meaning of fields that didn't change here** - especially the bitmask fields (regs 14, 15, 19) and the slow-varying counter (reg 17).

## Reg 11 transition

**Reg 11** changed from `0x00BA` (186) to `0x0174` (372) at 07:23:42.876 - just 3.5 seconds into the capture. Decoded as `(SoC_float * 100)`: 1.86% -> 3.72%. The small magnitude is curious - if reg 11 were main-pack SoC, the values would be in the 90s for a normally-charged pack. Plausible that reg 11 is some auxiliary "SoC-like" metric (cell-level SoC for the most-loaded cell? balancing budget? lifetime usage as fractional unit?), not main pack SoC.

A capture starting before the inverter brings the BMS up would clarify.

## Cross-reference

For the original empirical analysis (raw hex traces, Ken's first-pass interpretations), see [NOTES.md](../NOTES.md) ("Holding Registers" section).

The interpretations above merge Ken's observations with static analysis of the BMS firmware (function `0x0800d534` = init, `0x0800d584` = update, both populate the SRAM table at `0x200039C0`).
