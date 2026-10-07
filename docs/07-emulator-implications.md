# Emulator implications

Design rules and pitfalls for implementing a Modbus device that emulates a GivEnergy LV BMS. Such an emulator could be used to:

- Make a third-party LiFePO4 battery work with a GivEnergy inverter
- Bench-test inverter behaviour without a real battery
- Build a multi-battery aggregator that presents as N "virtual" GivEnergy batteries

The spec below is derived from the BMS firmware static analysis ([04](04-bms-firmware.md)), inverter firmware analysis ([05](05-inverter-firmware.md)), and real wire captures ([06](06-wire-captures.md)).

A working implementation of these rules is the GivEnergy LV RS485 inverter module for Battery-Emulator, in [abedegno/Battery-Emulator#1](https://github.com/abedegno/Battery-Emulator/pull/1). It presents a battery that Battery-Emulator reads (for example a Growatt LV CAN pack) to a G3 LV inverter as a GivEnergy battery.

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

If the emulator misses too many responses, the inverter raises a "BMS comms failure" status bit (about 20 main-loop ticks without a response on the variants in [05](05-inverter-firmware.md#inverter-side-validation-rules)). It will keep retrying though - a brief glitch is recoverable. On a G3 LV the DSP waits about 30 s without a valid reply, then zeroes both current limits and the SoC and sets the fault. The next valid reply clears it.

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
| Pack current | abs value < 60000 (signed 32-bit) | Probably flagged |

Stay inside these envelopes for portable emulation. Realistic LiFePO4 values (~3.2-3.4 V/cell at typical SoC, ambient temperature) easily satisfy them.

For a worked design of a specific case, a Seplos BMS battery on a GivEnergy G3 inverter, see [11-seplos-emulator-design.md](11-seplos-emulator-design.md).

## Polling cadence to expect

Driven by the inverter:

| Query | Cadence | Response size |
|---|---|---:|
| HR poll (device 1 only) | every ~245 ms (range 231-481 ms) | 61 bytes |
| IR Block 1 (per device) | twice, 10 s apart, in every ~200 s sweep on a G3 (one IR poll every ~10 s, one device at a time; see [03](03-input-registers.md)) | 48 bytes |
| IR Block 2 (per device) | about every 200 s on a G3 | 44 bytes |
| IR Block 3 (per device) | about every 200 s on a G3 | 46 bytes |
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
| 5-9 | ASCII serial, 10 characters | E.g. `"EM2024G001"`. Five registers, two characters each. IR Block 1 carries the same serial padded to 20 bytes |
| 10 | `0xFFFF` | Reserved |
| 11 | Total Ah of batteries online | Should normally be a fixed value based on actual capacity of batteries.  Can change if pack goes 'offline'. On a G3 LV this is the capacity the DSP scales the HR111/HR112 current caps (capacity x % + 1.5 A) and forced charge and discharge power by, from the 50th reply on. It is clamped at 10000 and not checked against any battery model. Never send 0: it drops those caps to 1.5 A and 2 A |
| 12 | `0x0030` (48) | Hardware-rev constant |
| 13 | `0x0BCE` (3022) | Firmware version - claim BMS 3022. A G3 LV inverter reads the charge and discharge limits from HR26/27 only when this is 3011 or higher; below that it uses HR25 for both. |
| 14 | `0x0000` | Status flag |
| 15 | `0x0000` | 3-flag composite. A G3 LV ignores bit 0 below 100% SoC, and at 100% bit 0 cancels its "battery full" block and the G3 charges on at 24% of rated power, so send 0 |
| 16 | `0x0000` | Mode/state |
| 17 | value that changes once per second | A real BMS derives it from its clock, mostly stepping by +1 each second. Incrementing once per second is the closest simple match; whether any inverter checks it is not known. |
| 18 | `0x389D` | High half of the same clock hash; it changes about twice a day (see [02](02-holding-registers.md)). A constant is fine for short runs. |
| 19 | BMS status (normally `0x00CE` or `0x00CF`) | 8-flag composite, see [02-holding-registers.md](02-holding-registers.md). On a G3 LV, keep bit 2 set (clear forces a charge of at least 300 W), don't leave bits 0 and 1 both clear, and only set bit 5 if you want a small forced discharge |
| 20 | Alarms | normally `0x0000`, see [02-holding-registers.md](02-holding-registers.md). On a G3 LV, bit 2 stops charging at once and bit 3 cuts the discharge limit to 10% of rated power (see below) |
| 21 | Battery state of charge (0%-100%) | If returning SoC |
| 22 | Battery voltage | Units of 0.01V |
| 23 | Primary pack current | 0.01 A units, `0x0000` for idle, or read from your real battery.  If emulating multiple battery packs, divide actual current by number of packs |
| 24 | Battery temperature | In degrees C |
| 25 | `0x2328` (9000) | Current limit = 90.00 A. A G3 LV uses it for both limits only when HR13 is below 3011 |
| 26 | Charge limit in 0.01A | Controls the inverter max charge power (1000 = ~500W). A G3 LV uses it as the charge limit too; see the G3 LV note in [02-holding-registers.md](02-holding-registers.md). |
| 27 | Discharge limit in 0.01A | Controls the inverter max discharge power (1000 = ~500W). A G3 LV uses it as the discharge limit too; see the same note. |

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
[2-byte BE pack voltage 0.001V]     ; a second reading, close to the first but not identical
[4-byte BE pack current, mA, signed] ; + = charge. e.g. FF FF FF 35 = -203 mA
[4-byte BE calibrated capacity 0.01 Ah] ; e.g. 00 00 4B C0 = 193.92 Ah
[4-byte BE design capacity 0.01 Ah]     ; 00 00 48 A8 = 186.00 Ah
[4-byte BE remaining capacity 0.01 Ah]  ; SoC = remaining / calibrated x 100
[1-byte SoC %]                          ; 0-100
00 00
0E 10                              ; 0x0E10 or 0x0610 on my battery; meaning unknown
00 00 00 00 00                     ; reserved
[2-byte BE firmware version]        ; 0x0BCE = 3022
00
```

### IR Block 3 (count=20)

40-byte data section:

```
[16 x 2-byte BE cell voltages, raw mV]   ; 32 bytes total. 3.30 V cell = 0x0CE4
[2-byte BE max temperature, 0.1 degC]    ; e.g. 00 B3 = 17.9 degC
[2-byte BE min temperature, 0.1 degC]    ; e.g. 00 A5 = 16.5 degC
[2-byte BE max cell voltage mV]
[2-byte BE min cell voltage mV]
```

## Stopping a charge on a G3 LV

A G3 LV never takes a charge voltage from the battery. It measures the pack itself and charges until the battery lowers HR26 or sets HR20 bit 2, apart from its own taper by SoC from 90%. It also stops by itself 30 s after HR21 reaches 100%, unless HR15 bit 0 is set, and it doesn't charge again until HR21 has been below 99% for 5 s (see [05-inverter-firmware.md](05-inverter-firmware.md#what-the-dsp-does-with-the-bms-status-registers)). So the emulator decides where the pack stops, through HR26, HR20 and the SoC it reports. These rules follow from the D316 DSP firmware (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)):

1. **Cut HR26 as the pack nears full.** The inverter keeps charging at up to its full rate until HR26 drops. Taper HR26 by the highest cell voltage, as the GivEnergy BMS does, not by SoC. If the SoC the emulator reports runs behind the pack's real state, the inverter's own SoC taper starts too late, and the pack can reach its voltage knee at full current.
2. **HR26 = 0 is not a hard stop.** The DSP never lets the HR26 path go below 1.00 A, so about 1 A still flows until its "battery full" block sets after 30 s.
3. **HR20 bit 2 is the strongest stop.** Outside a battery calibration it sets the DSP's charge limit to zero at once, whatever else asks for charge, and it raises no DSP fault. My GivEnergy battery set it at the end of a top-up at full charge. Set it with HR26 = 0 at a cell over-voltage stop and on a fault.
4. **Never run a battery calibration on an emulated battery.** During a calibration (inverter setting HR29 non-zero) the DSP holds both current limits at 8 A or more, never sets its "battery full" block, raises its voltage maximum by 5%, and HR20 bit 2 still lets 5% of rated power through. The ARM also uses HR20 bits 2 and 3 as the "full" and "empty" end points of the calibration, so an emulator that sets them could end it early with a wrong capacity.
5. **Keep HR109 = 1.** Any other value switches the DSP to a short HR17 to HR25 poll. If that happens while the inverter runs, the DSP reads HR26 and HR27 from past the end of the short reply and gets nonsense limits (see [05-inverter-firmware.md](05-inverter-firmware.md#inverter-settings-that-change-the-bms-link)).
6. **Latch the trickle at the top, and release it on a lower cell voltage than the cut.** A real BMS holds its reduced charge limit until the pack comes off the top - discharge starting, or the highest cell falling to about 3.40 V - not for a fixed time. An emulator that follows cell voltage live and reopens the charge limit as soon as the voltage relaxes after the current drops will invite the inverter to push current back into a full pack, over and over. Give the trickle and its release separate thresholds (hysteresis): cut at the top-of-charge voltage, and don't release until the cell has fallen further, once the pack is actually discharging.
7. **Check HR98.** The DSP's over-voltage trip is at HR98 `battery_high_voltage_protection_limit` + 1.0 V (1 s in total) or + 2.0 V (40 ms in total), on the inverter's own voltage reading, and the trip switches the battery converter off both ways. HR98 is 58.5 V on my inverter, but the DSP's default is 56.0 V, which puts the trip at 57.0 V. The inverter's reading is higher than the pack's: 0.2 V to 0.3 V at rest and about 1.3 V at 60 A on my system. Choose the pack's charge ceiling with that margin below the trip, and read HR98 again after any firmware update or settings change.
8. **The pack's own BMS is the last line.** Keep its cell over-voltage protection in place.

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

4. **Forgetting to echo FC=06 writes** - echo them unchanged, as the real battery does. A G3's DSP sends its FC=06 writes on counters and doesn't act on the reply ([05](05-inverter-firmware.md)), and none appeared in over two million frames of my G3 captures, so a missing echo is unlikely to matter on a G3. Other variants are unconfirmed.

5. **Treating the SoC as display only** - the inverter stops discharging when the SoC in HR21 reaches its 4% floor. The G3 LV DSP stores HR21's low byte as its SoC (`0xD506`) and runs its floor check on that, not on the IR Block 2 SoC. If an emulator passes through a third-party battery's SoC, that value decides how deeply the battery is discharged. Scale it if 4% on the inverter should leave a margin above the battery's own cut-off, and keep HR21 and the Block 2 SoC the same. HR21 is polled every ~245 ms, so the stop is quick: in a forced discharge at about 71 A, mine stopped within about a second of HR21 reading 4%, and in the 90-hour G3 capture the current reached zero 0.5 to 5.3 s after HR21 first read 4%. My 3020 battery doesn't soften the stop. Its HR27 (discharge limit) and HR20 (alarms) stayed unchanged all the way down to 4%. The 9.5 kWh battery (BMS firmware 4009) cuts HR27 to 100 A and then 60 A near the bottom, and the 60 A cut brought the current down from about 67 to 69 A to about 63 A in af987's captures, but it still lets about 63 A flow until HR21 reaches the floor. So the hard stop always comes from the inverter, from whatever SoC field it reads. See [06-wire-captures.md](06-wire-captures.md#discharge-stops-at-the-4-soc-floor) and [06-wire-captures.md](06-wire-captures.md#discharge-to-the-reserve-and-a-full-charge-27-29-september).

6. **Slow CRC implementation** - if you use a bit-shift CRC for every response, double-check your latency. A 61-byte HR response means CRCing 59 bytes 4 times per second; cheap on a Pi, marginal on small AVRs. Use the table-based implementation for deterministic timing.

7. **Passing a third-party pack's SoC straight through.** A GivEnergy battery has a hidden buffer: my 8.2 kWh Gen 1 pack is built from 200 Ah cells but reports only 160 Ah usable to the inverter, and even at the inverter's own 100% the cells still had headroom below their real top (see [06-wire-captures.md](06-wire-captures.md#discharge-to-the-reserve-and-a-full-charge-27-29-september)). So the inverter's 0% to 100% window is really a narrower slice of the cells' true range at both ends. A third-party pack usually doesn't carry the same buffer, so an emulator that reports its raw SoC will let the inverter cycle it much deeper than a genuine GivEnergy battery ever sees - all the way to the third-party pack's own cut-offs, not a comfortable margin above them. Scale the SoC window instead of passing it through raw: for example, present 10% to 95% of the real SoC as the inverter's 0% to 100%, so the real pack keeps its own margin at both ends.
