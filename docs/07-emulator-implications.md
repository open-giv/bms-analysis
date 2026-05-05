# Emulator implications

Design rules and pitfalls for implementing a Modbus device that emulates a GivEnergy LV BMS. Such an emulator could be used to:

- Make a third-party LiFePO4 battery work with a GivEnergy inverter
- Bench-test inverter behaviour without a real battery
- Build a multi-battery aggregator that presents as N "virtual" GivEnergy batteries

The spec below is derived from the BMS firmware static analysis ([04](04-bms-firmware.md)), inverter firmware analysis ([05](05-inverter-firmware.md)), and real wire captures ([06](06-wire-captures.md)).

## Hard requirements (the inverter will reject mismatches)

### 1. Wire format

| Item | Value |
|---|---|
| Bus | RS485, 9600 baud, 8N1 |
| Modbus variant | Modbus-RTU |
| CRC | CRC-16, polynomial `0xA001`, init `0xFFFF`, low byte first on the wire |
| Inter-frame silence | >=3.5 char times (~3.6 ms at 9600 baud) |

### 2. Function code support

The emulator must respond to:

- **FC=3** (read holding registers) - standard Modbus framing for both request and response
- **FC=4** (read input registers) - standard request, **non-standard response framing** (see below)
- **FC=6** (write single holding register) - echo back the request unchanged

Other FCs should return Modbus exception 0x80|FC with code 1 ("Illegal Function") - or just not respond at all. The inverter never sends them in steady-state, so this branch isn't exercised often.

### 3. The FC=4 non-standard response format

This is the main thing a stock Modbus library will get wrong:

```
Standard Modbus FC=4:           device | FC | byte_count(1) | data | CRC
GivEnergy BMS FC=4:             device | FC | addr_echo_hi | addr_echo_lo | data | CRC
```

The emulator must echo the request's start address (2 bytes, big-endian) in place of the byte_count. Data length is implicit from the request's count x 2.

A stock pymodbus / umodbus / similar will produce standard FC=4 frames - which the inverter rejects on CRC mismatch. **You must implement FC=4 framing manually** or fork the library.

FC=3 responses are standard - byte_count works fine there.

### 4. Latency budget

Respond within ~100 ms of receiving a complete request. Real BMS turnaround is 90-114 ms for HR (FC=3) and 84-89 ms for IR (FC=4). Plenty of headroom on a Pi or ESP.

If the emulator misses too many responses, the inverter raises a "BMS comms failure" status bit (~20 missed polls in a row). It will keep retrying though - a brief glitch is recoverable.

### 5. Device address

The emulator answers to the device address it's been configured as. The inverter:

- HR-polls **device 1 only** (always - no rotation)
- IR-polls **devices 1, 2, 3, 4, 5** in rotation (regardless of population)

If you only emulate one battery at device 1, the inverter will still try devices 2-5. Two options:

- **Multi-battery emulation**: respond as multiple devices (1 + 2 + ... up to 5).
- **Single-battery emulation**: respond only as device 1; let queries to other devices time out, or return the documented "absent device" pattern (see [03-input-registers.md](03-input-registers.md)). The inverter handles missing devices gracefully.

### 6. Value envelopes

The strictest validation seen across inverter variants:

| Field | Acceptable range | On out-of-range |
|---|---|---|
| Per-cell voltage | strictly between 2200 and 3700 mV | Silently dropped; UI shows "stuck" cell |
| Temperatures | strictly between -30.0 and +70.0 degC | Same silent-drop |
| Pack current | abs value < 60000 (signed int16) | Probably flagged |

Stay inside these envelopes for portable emulation. Realistic LiFePO4 values (~3.2-3.4 V/cell at typical SoC, ambient temperature) easily satisfy them.

## Polling cadence to expect

Driven by the inverter:

| Query | Cadence | Response size |
|---|---|---:|
| HR poll (device 1 only) | every ~245 ms (range 231-481 ms) | 61 bytes |
| IR Block 1 (per device) | once per ~12 s rotation cycle | 48 bytes |
| IR Block 2 (per device) | once per ~12 s rotation cycle | 44 bytes |
| IR Block 3 (per device) | once per ~12 s rotation cycle | 46 bytes |
| FC=06 mode-change writes | event-driven (charge enable, BMS reset, force-charge); not steady-state | 8 bytes echo |

The inverter waits for response completion before issuing the next query, so there's no bus contention for the emulator to handle.

## Recommended values for an emulator

Plausible defaults that pass validation:

### HR(0..27) responses

See [02-holding-registers.md](02-holding-registers.md) for full layout. Key values:

| Reg | Value | Notes |
|---:|---|---|
| 0 | `0x0065` (101) | Fixed device-marker constant - always send this |
| 1-4 | `0xFFFF` x 4 | Reserved / unused |
| 5-9 | ASCII serial padded to 20 chars | E.g. `"EM2024G001          "` (ends with NUL byte) |
| 10 | `0xFFFF` | Reserved |
| 11 | `0x00BA` initially | Possibly transitions to `0x0174` after some condition - emulator can leave it static at `0x00BA` |
| 12 | `0x0030` (48) | Hardware-rev constant |
| 13 | `0x0BCE` (3022) | Firmware version - claim BMS 3022 |
| 14 | `0x0000` | Status flag |
| 15 | `0x0000` | 3-flag composite |
| 16 | `0x0000` | Mode/state |
| 17 | counter that ticks ~once per query | E.g. start at `0x114B` and increment |
| 18 | `0x389D` | Constant device hash - any plausible value works |
| 19 | `0x00CE` or `0x00CF` | 8-flag composite, occasionally toggles |
| 20 | `0x0000` | |
| 21 | SoC-related, typically 90-100 (`0x5A`-`0x64`) | If returning SoC |
| 22 | `0x14C0` | Some narrow-drift field |
| 23 | Signed pack current, 0.01 A units | E.g. `0x0000` for idle, or read from your real battery |
| 24 | `0x0011` | |
| 25 | `0x2328` (9000) | Current limit = 90.00 A |
| 26 | Some 16-bit value that varies; identical to reg 27 | |
| 27 | Same as reg 26 | |

### IR Block 1 (count=21)

42-byte data section after the 4-byte response header:

```
[serial 20 bytes ASCII padded with spaces, NUL-terminated]
00 00                              ; reserved
[5 x 2-byte temperatures, 0.1 degC BE]   ; e.g. 00 C8 00 C8 00 C8 00 C8 00 C8 = 20.0 degC x 5
00 01                              ; flag
00 08                              ; "USB / accessory present" - claim 8 to mimic real BMS
00 00 00 00 00 00                  ; reserved
```

### IR Block 2 (count=19)

38-byte data section:

```
10                                 ; cell count = 16
[2-byte BE cycle count]             ; e.g. 00 00 = 0 cycles for new emulator
00 00                              ; reserved
[2-byte BE pack voltage 0.001V]     ; e.g. 53.000 V = 0xCEE8
[2-byte BE pack voltage 0.001V]     ; same value (duplicate readout)
FF FF FF 35 00 00                  ; mostly fixed pattern
[2-byte BE calibrated capacity 0.1 Ah]  ; e.g. 0x4BC0 = 193.92 Ah
00 00
[2-byte BE design capacity 0.1 Ah]      ; 0x48A8 = 186.00 Ah
00 00
[2-byte BE remaining capacity 0.1 Ah]   ; computed from SoC x design capacity
[1-byte SoC %]                          ; 0-100
00 00
0E 10                              ; constant 3600
00 00 00 00 00                     ; reserved
[2-byte BE firmware version]        ; 0x0BCE = 3022
00
```

### IR Block 3 (count=20)

40-byte data section:

```
[16 x 2-byte BE cell voltages, raw mV]   ; 32 bytes total. 3.30 V cell = 0x0CE4
00 B3 00 A5                              ; cell-level diagnostics (varies)
[2-byte BE max cell voltage mV]
[2-byte BE min cell voltage mV]
```

## Test methodology without a real inverter

Before having access to a real GivEnergy inverter, the emulator can be validated by:

1. **Unit tests against captured wire data** - feed the emulator a sequence of recorded request frames, verify byte-for-byte that its responses match real BMS responses. The reference captures (cold_start.log etc.) provide ~830 HR exchanges and ~14 IR exchanges of test vectors.

2. **Replay harness over a virtual serial pair** - use `socat` to create a virtual TTY pair, run the emulator on one end and a "fake inverter" replay tool on the other.

3. **Side-by-side bus test** - run the emulator alongside a real battery on the same RS485 bus at a different device address, compare its responses to the real one.

When the dongle / real inverter is available, end-to-end testing is straightforward: power-cycle the inverter with the emulator on the bus, verify the inverter's UI shows the emulated battery and reports plausible values.

## Common pitfalls

1. **Using a stock Modbus library and shipping it without testing FC=4 responses on a real inverter** - the byte_count vs addr_echo difference will silently fail in a way that looks like a CRC issue.

2. **Assuming Block 3 is 21 registers** - Ken's NOTES.md documents `count=21` but that was a misread. Both wire captures and FA-firmware static analysis confirm `count=20`. If you respond with 21 registers (42 bytes data) when 20 (40 bytes) was requested, the inverter will reject the frame.

3. **Returning out-of-range cell voltages** - even briefly. The strict variants silently filter and use the previous value, so a single bad poll doesn't get logged - but it also doesn't update. The inverter UI will show stale data, which is confusing to debug.

4. **Forgetting to echo FC=06 writes** - the inverter retries indefinitely on a missing FC=06 ACK. This stalls the bus and HR/IR polling resumes only after the FC=06 retry exits.

5. **Slow CRC implementation** - if you use a bit-shift CRC for every response, double-check your latency. A 56-byte HR response means CRCing ~58 bytes 4 times per second; cheap on a Pi, marginal on small AVRs. Use the table-based implementation for deterministic timing.
