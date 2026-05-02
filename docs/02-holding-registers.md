# Holding registers (FC=3)

The inverter polls a single block of 28 holding registers (offsets `0x0000`-`0x001B`) from **slave 1 only**, every ~245 ms. This is the highest-rate query on the bus and carries the BMS's real-time status.

## Poll request and response shape

```
Request:   01 03 00 00 00 1C 44 03                     (8 bytes; slave=1, FC=3, start=0, count=28, CRC)
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
| Slave addressed | 1 only (the primary battery; HR is never polled to other slaves) |

The slave-1 hard-coding is confirmed both empirically and from inverter firmware analysis: the FA-series Gen 3 builder writes `movs r0, #1; strb r0, [sp]` for the slave byte unconditionally on the HR path.

## Register layout

The 28 registers (= 56 bytes) decoded at the byte level:

| Reg | Bytes | Empirical observation | Firmware-derived interpretation |
|----:|---|---|---|
| 0   | 0-1   | constant `0x0065` (101) | Init writes literal `0x65`. Likely a **fixed protocol/device marker constant** (not the slave address - that's set by dipswitches). |
| 1-4 | 2-9   | constant `0xFFFF` x 4 | Never written after the 0xFFFF init. **Truly unused / reserved.** |
| 5-9 | 10-19 | ASCII serial number (e.g. `DX2319G279`) | 5 halfwords copied big-endian from a 10-byte SRAM struct. |
| 10  | 20-21 | constant `0xFFFF` | Never written. **Unused.** |
| 11  | 22-23 | varies (e.g. `0x00BA -> 0x0174` early in capture) | Computed by helper functions in the firmware. **State field** (a single transition was observed early in cold_start). Possibly a charge/discharge state marker. |
| 12  | 24-25 | constant `0x0030` (48) | Init writes literal `0x30`. Possibly a **hardware revision** field. |
| 13  | 26-27 | constant `0x0BCE` (3022) | Confirmed: `movw r0, #0xbce; strh r0, [r4, #0x1a]`. **BMS firmware version.** |
| 14  | 28-29 | constant `0x0000` early in capture | Set to 0 or 1 from a flag byte. **Boolean status** (not yet observed transitioning - needs labelled captures). |
| 15  | 30-31 | constant `0x0000` early in capture | OR-mask of 3 conditional bits (`#1`, `#2`, `#4`). **3-flag composite status.** |
| 16  | 32-33 | constant `0x0000` early in capture | Single byte loaded from RAM. **Mode/state byte.** |
| 17  | 34-35 | varies slowly (~142 distinct values, range `0x114A`-`0x1219`) | Slow-varying counter built from 6 bytes via a digit-decoder helper (`bl 0x80011dc` in BMS firmware). |
| 18  | 36-37 | **constant** `0x389D` (14493) across all 829 captured responses | Confirmed constant. **Static device hash / production code high word.** Earlier hypothesis that 17+18 form a 32-bit pair was wrong - they're independent. |
| 19  | 38-39 | toggles between `0x00CE` and `0x00CF` | OR-mask of 8 conditional bits. **8-flag composite status / fault-warning byte.** One bit (bit 0) toggles. |
| 20  | 40-41 | constant `0x0000` | Not yet identified. |
| 21  | 42-43 | 3 distinct values: `0x005D` / `0x005E` / `0x005F` | Possibly **SoC %** (93 / 94 / 95 - matches a slowly-changing percentage). Decreased over the capture window. |
| 22  | 44-45 | 7 distinct values, range `0x14BF`-`0x14C5` (5311-5317) | Small-variation field. Could be a temperature sensor or scaled voltage; not yet confirmed. |
| 23  | 46-47 | 85 distinct values, range spans `0x0000` and `0xFFFF` (signed +/-) | **Signed pack current**, probably in 0.01 A units. Sign flips between samples confirm it's signed. |
| 24  | 48-49 | nearly constant `0x0011` (17) | Not yet identified. |
| 25  | 50-51 | constant `0x2328` (9000) | Written via `(raw << 4) & 0x3FFFC` in the firmware. 9000 in 0.01 A units = **90.00 A continuous discharge current limit**. |
| 26  | 52-53 | 10 distinct values, range `0x20B2`-`0x4164` (varies with 27) | Identical to reg 27. **Duplicate readout** of the same field. |
| 27  | 54-55 | 10 distinct values, range `0x20B2`-`0x4164` (always equals 26) | Identical to reg 26. |

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

The most interesting observed transition: **reg 11** changed from `0x00BA` (186) to `0x0174` (372) at 07:23:42.876 - just 3.5 seconds into the capture. Cause unknown; speculation:

- A handshake / "BMS recognises the inverter" state transition
- A charge/discharge mode change
- A balancing-active flag

A capture starting before the inverter brings the BMS up would clarify.

## Cross-reference

For the original empirical analysis (raw hex traces, Ken's first-pass interpretations), see [NOTES.md](../NOTES.md) ("Holding Registers" section).

The interpretations above merge Ken's observations with static analysis of the BMS firmware (function `0x0800d534` = init, `0x0800d584` = update, both populate the SRAM table at `0x200039C0`).
