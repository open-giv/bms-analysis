# Input registers (FC=4)

The inverter polls input registers from each battery (devices 1..5) using FC=4 reads. Telemetry data including cell voltages, temperatures, capacities, and SoC is split across **three blocks**:

| Block | Start address | Count | Approximate purpose |
|---|---|---:|---|
| Block 1 | `0x0000` | 21 regs | Serial number + temperature sensors |
| Block 2 | `0x0015` | 19 regs | Cell count, cycles, pack voltage, capacities, SoC, firmware version |
| Block 3 | `0x0028` | 20 regs | Per-cell voltages + min/max cell voltage + reserved |

The blocks are not contiguous: there's a gap at register `0x003C` and beyond (not polled).

## Wire format

**FC=4 responses are non-standard** - they echo the request's start address in place of the standard byte_count field. See [01-protocol.md](01-protocol.md) for the full framing details. The data layouts below describe the bytes **after** the 4-byte response header (`device + FC + addr_echo_hi + addr_echo_lo`).

## Cadence

| Metric | Value |
|---|---|
| IR poll interval (per query) | ~10-12 seconds between repetitions of the same query |
| Full device x block sweep | ~3 minutes for 5 devices x 3 blocks |
| BMS turnaround latency | 84-89 ms (faster than HR because responses are smaller) |
| Devices polled | 1, 2, 3, 4, 5 (all five regardless of which are populated) |

The inverter alternates IR polls into the gaps between HR polls. HR is the high-priority loop; IR is opportunistic telemetry collection.

## Device rotation

The inverter polls all five potential device addresses (1..5) regardless of how many batteries are actually present. Empty device slots return a distinctive "absent device" pattern (see end of this document).

## Block 1 (regs 0x0000 - 0x0014, 21 regs)

42 bytes of payload. Layout (offsets are within the 42-byte data section, after the 4-byte response header):

| Reg | Offset | Bytes | Field | Notes |
|---:|---:|---:|---|---|
| 0 | 0 | 20 | Serial number (ASCII, padded with spaces, NUL-terminated) | e.g. `XXXXXXXXXX` followed by 9 spaces and a NUL |
| 10 | 20 | 2 | (unknown / 0x0000) | Always observed as zero |
| 11 | 22 | 2 | Cell Temp 1 | 0.1 degC big-endian (e.g. `0x00AB` = 17.1 degC). See "Temperature encoding" note below. |
| 12 | 24 | 2 | Cell Temp 2 | 0.1 degC big-endian |
| 13 | 26 | 2 | Cell Temp 3 | 0.1 degC big-endian |
| 14 | 28 | 2 | Cell Temp 4 | 0.1 degC big-endian |
| 15 | 30 | 2 | BMS Temp | 0.1 degC big-endian |
| 16 | 32 | 2 | (unknown, observed `0x0001`) | Possibly a count or flag |
| 17 | 34 | 2 | (unknown, observed `0x0008`) | Possibly USB / accessory presence flag |
| 18 | 36 | 6 | (unknown, all zero) | Reserved / unused |

> **Temperature encoding**: the BMS firmware applies `subw r1, r1, #0xAAA` (i.e. subtract 2730) to each of the 5 temperature halfwords just before writing them to the TX buffer (flash addresses 0x0800_DF8C, 0x0800_DF98, 0x0800_DFA4, 0x0800_DFB0, 0x0800_DFBC). Internally the values are stored as `(decidegC + 2730)` - a positive-offset representation. The subw removes the bias before TX, so the **wire bytes are raw decidegC**, signed (negative temperatures will appear as 2's-complement int16). No decoder transform needed. The "absent device" `0xF556` sentinel is a natural side effect of the same encoding (see absent-device section).

Example response data (device 1 in cold_start.log, capture time 07:23:51):

```
58 58 58 58 58 58 58 58 58 58 20 20 20 20 20 20 20 20 20 20    ; "XXXXXXXXXX" + 10 spaces
00 00                                                          ; unknown
00 ab 00 b2 00 ad 00 a5 00 a9                                  ; 5 temperatures (17.1, 17.8, 17.3, 16.5, 16.9 degC)
00 01 00 08                                                    ; unknown flags
00 00 00 00 00 00                                              ; unknown / reserved
```

ASCII view: `XXXXXXXXXX          ......................`

## Block 2 (regs 0x0015 - 0x0027, 19 regs)

38 bytes of payload. Pack-level status and configuration.

| Offset | Bytes | Field | Notes |
|---:|---:|---|---|
| 0 | 1 | Number of cells | Hex digit; `0x10` = 16 cells |
| 1 | 2 | Number of battery cycles | Big-endian uint16 (e.g. `0x02E1` = 737 cycles) |
| 3 | 2 | (unknown / `0x0000`) | |
| 5 | 2 | (unknown, varies) | Possibly a voltage in mV (e.g. `0xCD33` = 52.531 V if interpreted as 0.001 V scale) |
| 7 | 2 | Pack voltage | Likely 0.001 V scale (e.g. `0xCF85` = 53.125 V) |
| 9 | 6 | (unknown, mostly 0xFF / variable) | |
| 15 | 2 | Battery capacity (calibrated) | 0.1 Ah units, big-endian (e.g. `0x4BC0` = 19392 = 1939.2 Ah... actually 193.92 Ah - see notes) |
| 17 | 2 | (unknown / `0x0000`) | |
| 19 | 2 | Design capacity | 0.1 Ah units (e.g. `0x48A8` = 18600 = 186.00 Ah) |
| 21 | 2 | (unknown / `0x0000`) | |
| 23 | 2 | Remaining capacity | 0.1 Ah units (e.g. `0x467B` = 18043 = 180.43 Ah) |
| 25 | 1 | State of Charge | Direct % (e.g. `0x5D` = 93%) |
| 26 | 2 | (unknown / `0x0000`) | |
| 28 | 2 | (unknown, often `0x0E10` = 3600) | Possibly a time-in-mode counter or rate constant |
| 30 | 5 | (unknown, all zero) | Reserved |
| 35 | 2 | BMS firmware version | E.g. `0x0BCE` = 3022 |
| 37 | 1 | (unknown / `0x00`) | |

> **Note**: capacity unit: Ken's [NOTES.md](../NOTES.md) initially documented these as mAh, then corrected to deci-Ah. So `0x48A8` = 18600 in raw units = **186.00 Ah** when interpreted as 0.1 Ah.

Example (device 1 in cold_start.log):

```
10 02 e1 00 00 cd 33 cf 85 ff ff ff 35 00 00 4b c0 00 00 48
a8 00 00 46 7b 5d 00 00 0e 10 00 00 00 00 00 0b ce 00
```

## Block 3 (regs 0x0028 - 0x003B, 20 regs)

40 bytes of payload. Per-cell voltages and min/max summary.

> **Note on count**: This firmware variant requests **count = 20** (`0x14`) registers. Some early documentation suggested 21 - that was a misread. Both empirical wire captures and inverter firmware static analysis confirm count=20.

| Reg | Offset | Bytes | Field | Notes |
|--:|---:|---:|---|---|
| 40 | 0 | 32 | 16 cell voltages | Each cell = 2 bytes big-endian, **raw mV**, no offset. E.g. `0x0D07` = 3335 mV. |
| 56 | 32 | 2 | Max cell temp | Tracks very closely to the max temp in block 1 |
| 57 | 34 | 2 | Min cell temp | Tracks very closely to the min temp in block 1 |
| 58 | 36 | 2 | Max cell voltage | Raw mV. `0x0D09` = 3337 mV (slightly higher than highest individual cell). |
| 59 | 38 | 2 | Min cell voltage | Raw mV. `0x0D05` = 3333 mV. |

> **Cell voltage encoding** (per-cell): cell voltages at offsets 0..31 are **raw millivolts** big-endian, 2 bytes per cell, no offset. The 16-cell loop in the FC=4 handler at flash `0x0800_E0A0..0x0800_E0BE` writes them directly without applying any bias.
>
> **Aggregate-field encoding (offsets 32-35)**: these two fields ARE encoded with a `-2730` mV offset (the firmware applies `subw r1, r1, #0xAAA` at flash 0x0800_E0C0 / 0x0800_E0CE before writing them to TX). Decoders need to add 2730 mV to recover the user-facing value.
>
> The **max / min cell voltage at offsets 36-39 are raw mV** (no `subw` applied) - same encoding as the per-cell values.
>
> See [04-bms-firmware.md](04-bms-firmware.md) for the firmware code paths and the full set of `-2730`-encoded fields.

Example (device 1 in cold_start.log):

```
0c f4 0c f5 0c f5 0c f7 0c f7 0c f7 0c f7 0c f8 0c f8 0c f8    ; cells 1-10
0c fa 0c fa 0c fa 0c fa 0c fa 0c fc                            ; cells 11-16
00 b3 00 a5                                                    ; unknown (179, 165)
0c fc 0c f4                                                    ; max=3324 mV, min=3316 mV
```

This pack is ~3.31 V/cell - matching giv_tcp's reported "Battery_Cell_X_Voltage": 3.31 type values.

## "Absent device" pattern

Devices 3, 4, 5 in Ken's setup don't have real batteries (he has 2). The inverter still polls them and **the slot returns a distinctive empty-but-valid response** for each block:

### Block 1 absent-device response (42 bytes data):

```
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00    ; serial = all zeros
00 00                                                          ; unknown = 0
f5 56 f5 56 f5 56 f5 56 f5 56                                  ; 5 temp slots = 0xF556 each (= 62806)
00 00 00 00                                                    ; flags = 0
00 00 00 00 00 00                                              ; reserved
```

The `f5 56 f5 56 f5 56 f5 56 f5 56` pattern in the temperature region is distinctive - 5 repetitions of `0xF556` (= 62806 unsigned, or -2730 signed-int16).

This is **not an explicit "no sensor" special value** - it's a natural consequence of the temperature-encoding `subw`. The firmware stores temperatures internally as `(decidegC + 2730)`. For an absent battery slot, the internal value is `0`, so the `subw r1, r1, #0xAAA` at TX produces `0 - 2730 = -2730 = 0xF556` (uint16). An emulator that supports multi-battery mode just emits `0xF556` for empty temp slots without any special-case logic. Same explanation for the `0xF556` at Block 3 max/min slots (those positions are also driven by `subw`'d code paths in the firmware's RAM init / formatting).

### Block 2 absent-device response: all zeros (38 bytes)

### Block 3 absent-device response (40 bytes data):

```
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00    ; cells = 0
00 00 00 00 00 00 00 00 00 00 00 00                            ; reserved
f5 56 f5 56                                                    ; max/min = 0xF556 each
00 00 00 00                                                    ; reserved
```

Important for emulators that want to support multi-battery configurations: if you only emulate one battery at device 1, the inverter will still poll devices 2-5. Either respond with the absent-device pattern (cleanest), or don't respond at all (the bus times out, then HR resumes).

## Firmware-side source mapping (empirical, Unicorn-verified)

The wire-byte layouts above were originally derived from Ken's wire captures. The BMS firmware (v3022) was subsequently driven directly under Unicorn to confirm, for every wire byte, the corresponding source position in the per-pack data struct that the firmware reads from. This section documents those source mappings so an emulator can populate the right per-pack offsets and trust the wire output will match.

### Per-pack struct

Each pack slot is **145 bytes (`0x91`)** at SRAM `0x20003D6A + slot * 145`, where `slot = min((slave - 1) & 0xff, 6)`. The clamp-to-6 allows slave addresses >= 7 to read from the 7th 145-byte slot beyond the documented 6 packs - an off-by-one quirk of the bounds check at flash `0x0801DF50`. Slaves 1..6 map to slots 0..5 cleanly.

### Storage rule

Multi-byte fields are stored **little-endian** in the per-pack struct and emitted **big-endian** on the wire. The handler reads each u16/u32 with a native LE load and writes byte-by-byte in BE order, so the net effect on the wire is a byte-swap of the source. Single-byte reads pass through unchanged.

### Block 1 source mapping (start=0x0000, count=21)

| Wire offset | Source pack offset | Encoding |
|---:|---|---|
| 0-20  | pack[0x02 .. 0x16] (21 bytes) | direct byte copy - the ASCII serial pad |
| 21    | (none) | constant 0 (padding) |
| 22-23 | pack[0x79] u16 LE | `(value - 0xAAA)` BE = decideg temp 1 |
| 24-25 | pack[0x7B] u16 LE | temp 2 |
| 26-27 | pack[0x7D] u16 LE | temp 3 |
| 28-29 | pack[0x7F] u16 LE | temp 4 |
| 30-31 | pack[0x81] u16 LE | temp 5 |
| 32-33 | pack[0x00 .. 0x01] u16 LE | byte-swap (semantic TBD; docs/03 main table shows "unknown, observed 0x0001") |
| 34    | (none) | constant 0 |
| 35    | pack[0x83] | direct byte (per docs/03 main table, "observed 0x08") |
| 36-41 | (none) | constant 0 |

### Block 2 source mapping (start=0x0015, count=19)

| Wire offset | Source pack offset | Encoding |
|---:|---|---|
| 0     | pack[0x2C] | byte (cell count per docs/03) |
| 1-2   | pack[0x2D .. 0x2E] u16 LE | byte-swap to BE (cycles) |
| 3-6   | pack[0x2F .. 0x32] u32 LE | byte-swap to BE |
| 7-8   | pack[0x33 .. 0x34] u16 LE | byte-swap to BE (pack voltage) |
| 9-12  | pack[0x35 .. 0x38] u32 LE | byte-swap to BE |
| 13-16 | pack[0x39 .. 0x3C] u32 LE | byte-swap to BE (calibrated capacity) |
| 17-20 | pack[0x3D .. 0x40] u32 LE | byte-swap to BE (design capacity) |
| 21-24 | pack[0x41 .. 0x44] u32 LE | byte-swap to BE (remaining capacity) |
| 25    | pack[0x88] | byte (SoC %) |
| 26-34 | pack[0x46 .. 0x4E] (9 bytes) | direct byte copy |
| 35-36 | pack[0x77 .. 0x78] u16 LE | byte-swap to BE = firmware version (3022 -> wire `0B CE`) |
| 37    | (none) | constant 0 |

### Block 3 source mapping (start=0x0028, count=20)

| Wire offset | Source pack offset | Encoding |
|---:|---|---|
| 0-31  | pack[0x53 .. 0x72] (16x u16 LE) | byte-swap per cell to BE; **raw mV, no offset** |
| 32-33 | pack[0x73 .. 0x74] u16 LE | `(value - 0xAAA)` BE |
| 34-35 | pack[0x75 .. 0x76] u16 LE | `(value - 0xAAA)` BE |
| 36-37 | pack[0x4F .. 0x50] u16 LE | byte-swap to BE (max cell mV) |
| 38-39 | pack[0x51 .. 0x52] u16 LE | byte-swap to BE (min cell mV) |

### Validation envelope (firmware-imposed)

The handler rejects requests where:

- `count == 0` or `count > 60` (0x3C)
- `start + count` would cross a 60-register block boundary

A request that fails validation produces a Modbus exception response (`slave | 0x84 | CRC`). All three documented blocks (0/21, 0x15/19, 0x28/20) fall inside their respective 60-byte sub-block, so legitimate inverter polls always pass.

### Methodology

Confirmed by driving the BMS firmware's FC=4 handler (`fc4_handler` at flash `0x0801DEBC`) directly under Unicorn Engine. For each block, the per-pack struct was pre-populated with distinctive markers at every byte position, the handler was invoked with R0 = RX-frame pointer (0x2000385C) and R1 = FC byte (4), and the resulting TX-buffer bytes at 0x200038C0 were compared against expected marker positions. The handler's slot-index logic was independently verified by populating multiple slots with different markers and varying the slave byte in the RX frame.

The fc4_handler entry address corrected an earlier off-by-4 noted in the working notes (`0x0801DEB8` -> `0x0801DEBC`); calling at the older address landed inside the preceding function and produced an empty addr-echo response with no body data.

## Cross-reference

For the original empirical analysis (raw hex traces, Ken's first-pass interpretations of all three blocks), see [NOTES.md](../NOTES.md) ("Input Registers" section).
