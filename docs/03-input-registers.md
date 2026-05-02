# Input registers (FC=4)

The inverter polls input registers from each battery (slaves 1..5) using FC=4 reads. Telemetry data including cell voltages, temperatures, capacities, and SoC is split across **three blocks**:

| Block | Start address | Count | Approximate purpose |
|---|---|---:|---|
| Block 1 | `0x0000` | 21 regs | Serial number + temperature sensors |
| Block 2 | `0x0015` | 19 regs | Cell count, cycles, pack voltage, capacities, SoC, firmware version |
| Block 3 | `0x0028` | 20 regs | Per-cell voltages + min/max cell voltage + reserved |

The blocks are not contiguous: there's a gap at register `0x003C` and beyond (not polled).

## Wire format

**FC=4 responses are non-standard** - they echo the request's start address in place of the standard byte_count field. See [01-protocol.md](01-protocol.md) for the full framing details. The data layouts below describe the bytes **after** the 4-byte response header (`slave + FC + addr_echo_hi + addr_echo_lo`).

## Cadence

| Metric | Value |
|---|---|
| IR poll interval (per query) | ~10-12 seconds between repetitions of the same query |
| Full slave x block sweep | ~3 minutes for 5 slaves x 3 blocks |
| BMS turnaround latency | 84-89 ms (faster than HR because responses are smaller) |
| Slaves polled | 1, 2, 3, 4, 5 (all five regardless of which are populated) |

The inverter alternates IR polls into the gaps between HR polls. HR is the high-priority loop; IR is opportunistic telemetry collection.

## Slave rotation

The inverter polls all five potential slave addresses (1..5) regardless of how many batteries are actually present. Empty slave slots return a distinctive "absent slave" pattern (see end of this document).

## Block 1 (regs 0x0000 - 0x0014, 21 regs)

42 bytes of payload. Layout (offsets are within the 42-byte data section, after the 4-byte response header):

| Offset | Bytes | Field | Notes |
|---:|---:|---|---|
| 0 | 20 | Serial number (ASCII, padded with spaces, NUL-terminated) | e.g. `DX2319G279` followed by 9 spaces and a NUL |
| 20 | 2 | (unknown / 0x0000) | Always observed as zero |
| 22 | 2 | Temperature sensor 1 | 0.1 degC units, big-endian (e.g. `0x00AB` = 17.1 degC) |
| 24 | 2 | Temperature sensor 2 | 0.1 degC units (e.g. `0x00B2` = 17.8 degC) |
| 26 | 2 | Temperature sensor 3 | 0.1 degC units |
| 28 | 2 | Temperature sensor 4 | 0.1 degC units |
| 30 | 2 | Temperature sensor 5 | 0.1 degC units |
| 32 | 2 | (unknown, observed `0x0001`) | Possibly a count or flag |
| 34 | 2 | (unknown, observed `0x0008`) | Possibly USB / accessory presence flag |
| 36 | 6 | (unknown, all zero) | Reserved / unused |

Example response data (slave 1 in cold_start.log, capture time 07:23:51):

```
44 58 32 33 31 39 47 32 37 39 20 20 20 20 20 20 20 20 20 20    ; "DX2319G279" + 10 spaces
00 00                                                          ; unknown
00 ab 00 b2 00 ad 00 a5 00 a9                                  ; 5 temperatures (17.1, 17.8, 17.3, 16.5, 16.9 degC)
00 01 00 08                                                    ; unknown flags
00 00 00 00 00 00                                              ; unknown / reserved
```

ASCII view: `DX2319G279          ......................`

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

Example (slave 1 in cold_start.log):

```
10 02 e1 00 00 cd 33 cf 85 ff ff ff 35 00 00 4b c0 00 00 48
a8 00 00 46 7b 5d 00 00 0e 10 00 00 00 00 00 0b ce 00
```

## Block 3 (regs 0x0028 - 0x003B, 20 regs)

40 bytes of payload. Per-cell voltages and min/max summary.

> **Note on count**: This firmware variant requests **count = 20** (`0x14`) registers. Some early documentation suggested 21 - that was a misread. Both empirical wire captures and inverter firmware static analysis confirm count=20.

| Offset | Bytes | Field | Notes |
|---:|---:|---|---|
| 0 | 32 | 16 cell voltages | Each cell = 2 bytes big-endian, raw mV. E.g. `0x0D07` = 3335 mV |
| 32 | 2 | (unknown, possibly cell-level diagnostic) | Varies |
| 34 | 2 | (unknown) | Varies |
| 36 | 2 | Max cell voltage | `0x0D09` = 3337 mV (slightly higher than the highest individual cell) |
| 38 | 2 | Min cell voltage | `0x0D05` = 3333 mV |

> **Cell voltage encoding**: Cell voltages on the wire are **raw millivolts**, big-endian, 2 bytes per cell. Despite the BMS firmware internally storing cells with a `-2730` offset (an artefact of inter-pack PACE protocol encoding), the wire format to the inverter is plain mV. Don't apply any offset in an emulator. See [04-bms-firmware.md](04-bms-firmware.md) for the firmware code path.

Example (slave 1 in cold_start.log):

```
0c f4 0c f5 0c f5 0c f7 0c f7 0c f7 0c f7 0c f8 0c f8 0c f8    ; cells 1-10
0c fa 0c fa 0c fa 0c fa 0c fa 0c fc                            ; cells 11-16
00 b3 00 a5                                                    ; unknown (179, 165)
0c fc 0c f4                                                    ; max=3324 mV, min=3316 mV
```

This pack is ~3.31 V/cell - matching giv_tcp's reported "Battery_Cell_X_Voltage": 3.31 type values.

## "Absent slave" pattern

Slaves 3, 4, 5 in Ken's setup don't have real batteries (he has 2). The inverter still polls them and **the slot returns a distinctive empty-but-valid response** for each block:

### Block 1 absent-slave response (42 bytes data):

```
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00    ; serial = all zeros
00 00                                                          ; unknown = 0
f5 56 f5 56 f5 56 f5 56 f5 56                                  ; 5 temp slots = 0xF556 each (= 62806)
00 00 00 00                                                    ; flags = 0
00 00 00 00 00 00                                              ; reserved
```

The `f5 56 f5 56 f5 56 f5 56 f5 56` pattern in the temperature region is distinctive - 5 repetitions of `0xF556` (= 62806 unsigned, or -2730 signed-int16). Almost certainly a "no sensor data" sentinel value.

### Block 2 absent-slave response: all zeros (38 bytes)

### Block 3 absent-slave response (40 bytes data):

```
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00    ; cells = 0
00 00 00 00 00 00 00 00 00 00 00 00                            ; reserved
f5 56 f5 56                                                    ; max/min = 0xF556 each
00 00 00 00                                                    ; reserved
```

Important for emulators that want to support multi-battery configurations: if you only emulate one battery at slave 1, the inverter will still poll slaves 2-5. Either respond with the absent-slave pattern (cleanest), or don't respond at all (the bus times out, then HR resumes).

## Cross-reference

For the original empirical analysis (raw hex traces, Ken's first-pass interpretations of all three blocks), see [NOTES.md](../NOTES.md) ("Input Registers" section).
