# Inverter firmware analysis

The GivEnergy inverter firmware can also be statically analysed. The most important takeaway from this analysis is structural rather than detail-level:

> **The wire protocol is invariant across inverter variants. One BMS spec covers all compatible inverters.**

## The invariant

The same GivEnergy Gen 2 LV BMS works with:

- AC 3.0 inverters
- Gen 1 Hybrid inverters
- Gen 2 Hybrid inverters
- Gen 3 Hybrid (FA-series) inverters

Since the BMS firmware (`BMS_ARM.bin` v3017/3020/3022) implements one Modbus dialect, every compatible inverter must speak that same wire protocol or the BMS won't respond. **The wire protocol is the constant; inverter firmware variations are an internal-implementation concern that doesn't reach the wire.**

This means an emulator that satisfies the BMS-side spec (see [docs/01](01-protocol.md), [docs/02](02-holding-registers.md), [docs/03](03-input-registers.md)) will be accepted by any LV-compatible GivEnergy inverter.

## Inverter variants surveyed

Static analysis covered the ARM firmware for several variants:

| Variant | Firmware files | Architecture |
|---|---|---|
| FA-series (Gen 3 Hybrid) | `FA_A1_xx.bin` (256 KB) + `FA_A2_xx.bin` (17 KB) + `FA_D1_xx.bin` | 3-MCU: ARM1 + ARM2 + DSP |
| A316 / Hybrid Gen 3 LV | `ARMStore.bin` (145 KB) + `DSPStore.bin` (131 KB) | ARM + DSP |

The community firmware archive files the A316-D316 package under "Hybrid Gen3 LV" and the FA packages under "PV String Inverter Gen3". Earlier versions of this table called A316 a Gen 1/2 hybrid. The FA label has not been rechecked.
| A920/A921/A922 / AIO | `ARMStore.bin` (126 KB) + `DSPStore.bin` (131 KB) | ARM + DSP |
| A214/D212 (older) | `ARMStore.bin` (118 KB) + `DSPStore.bin` (131 KB) | ARM + DSP |
| AC 3.0 | (not yet analysed) | unknown, but compatible with same BMS |

All ARM firmwares analysed contain:

- Canonical Modbus CRC-16 lookup tables (`auchCRCHi`, `auchCRCLo`)
- FC=3 / FC=4 / FC=6 builders
- The same wire-protocol output

## Internal differences between variants

While the wire format is constant, internal code varies substantially. Some examples:

- **FA-series uses a 3-MCU split**: a main ARM (STM32F105, 256 KB) does the BMS Modbus controller + scheduler + cloud reporting; a small ARM2 (STM32F103, 17 KB) handles sensor I/O over an inter-MCU link; the DSP handles power-electronics control. The BMS link is on USART2.

- **A316 uses a 2-MCU split** (ARM + DSP). The ARM decides what to read from the BMS and parses the answers, but the DSP drives the BMS RS485 bus (see [A316: the DSP runs the BMS bus](#a316-the-dsp-runs-the-bms-bus)). The firmware's load address is `0x08014000` (not `0x08000000`) - confirmed via the reset vector and PC-relative references to the CRC tables.

- **Some variants additionally talk to non-BMS devices on the same RS485 bus** - e.g. A316 has a parallel controller that polls device `0x11` (an energy meter / EMS / control unit) with FC=3 / FC=6, plus a 17-entry table walk over devices 1, 5, 6, 7, 8 (purpose unclear; possibly parallel-inverter or HV stack expansion). Neither of these talks to the LV battery the way the wire captures show.

The key observation: the LV-battery polling code in each variant produces the same on-the-wire bytes, even though the implementation paths differ.

## Inverter-side validation rules

The inverter validates BMS responses against various sanity checks. The strictest envelope seen across multiple firmware variants:

| Field | Acceptable range | Behaviour on out-of-range |
|---|---|---|
| Per-cell voltage | strictly between 2200 and 3700 mV (i.e. `(2200, 3700)` exclusive) | Silently dropped; RAM keeps last good value (no fault flag) |
| Temperatures | strictly between -30.0 and +70.0 degC | Same silent-drop behaviour |
| Pack current | absolute value < 60000 (signed 32-bit) | Likely flagged but not blocking |
| No-response timeout | ~20 main-loop ticks before raising "BMS comms failure" | Status bit raised; UI may show "BMS lost" |

The validation only applies to fields that come back in known-format responses (e.g. cell voltages are validated when the response byte_count matches expected = 40 for 20 cells x 2 bytes). Unknown-format responses skip validation entirely.

**For an emulator**: keep simulated values inside these envelopes. If you fall outside, the inverter silently drops the reading and uses the previous value - usually visible as "stuck" cell voltages in the inverter UI.

The values seen by Ken on the wire (e.g. the constant `0x2328` = 9000 = 90.00 A current limit, or `0x48A8` = 18600 = 186.00 Ah design capacity, or `0x0BCE` = 3022 firmware version) are **not** validated as specific magic constants by the inverter - they're informational and the emulator can return any plausible value.

## Implementation notes per variant

### FA-series (Gen 3, FA_A1_03.bin)

| Item | Flash address |
|---|---|
| CRC-16 byte-stride function | `0x0800_C950` |
| auchCRCHi / auchCRCLo tables | `0x0803_E2F4` / `0x0803_E3F4` |
| Modbus request builder (unified) | `0x0801_15B2` - `0x0801_19B0` |
| HR poll path (device=1, FC=3, count=0x1C) | `0x0801_1938` |
| IR rotation state machine | `0x0801_1812` - `0x0801_18A8` |
| BMS RS485 = USART2 | `0x4000_4400` |

Polling cadence in this firmware is event-driven (RX completion gates the next request). The observed ~245 ms HR cadence comes from a 200-tick throttle on a 1 ms SysTick plus the wire round-trip time at 9600 baud.

FA-series has FC=06 builders for mode-change events (charge enable, BMS reset, force-charge). These are not sent during steady-state polling but the emulator must echo them back correctly when triggered.

#### FA inverter dispatch table for HR0..HR27 (Modbus-TCP reads)

The FA inverter exposes HR readings on its Modbus-TCP API via `FUN_08015dcc` -- a big switch on register-index. For an emulator and bridge author, key observations:

- **HR3 and HR4 are firmware-duplicates**: both load identical bytes from `*0x2000c9d4`. The inverter exposes the same value for both. Emulators must supply identical values for HR3 and HR4 to avoid surprising the inverter.
- **HR21 / HR22 share a 32-bit word internally** (`{s16 SoC, u16 voltage}` packed at `0x2000c3a0`). Wire format unchanged; just an internal storage detail.
- **HR26 / HR27 share a 32-bit word internally**, with HR27 extracted via `asr #16` (signed upper halfword). The inverter treats HR27 as signed.
- **No unit scaling for HR23**: the inverter reads the raw u16 with no `sdiv` -- the wire bytes are passed through verbatim to whatever consumer reads them. Adjacent HR24 has explicit `sdiv 10`. HR8/9/10/18/20/30/42/43 also divide by 10; HR198 divides by 100. The lack of `sdiv` on HR23 doesn't tell us the wire unit by itself -- it tells us that whatever the wire unit is, that's the unit the inverter exposes downstream. **Wire unit determined empirically as centi-amps (0.01 A)**: Ken's app shows ~1.53 kW total at ~53 V across 2 paralleled packs = 14.4 A per pack, and HR23 wire = 1490, which only makes sense as 0.01 A units (149 A in deci-amps is impossible for these batteries). See [HR23 row in docs/02](02-holding-registers.md) for the full BMS-side derivation.

#### Dual current-sensor architecture

The FA inverter has **two independent current measurements**, often confused:

- **HR23** (Modbus RS485 from BMS): **centi-amps (0.01 A)**, signed. Computed by the BMS from its own ADC ring buffer + 5-segment piecewise polynomial calibration. Reports primary-pack current only. Wire bytes pass through the FA inverter's `FUN_08015dcc` exposure path unchanged.
- **`i_battery` at IR(51)** (Modbus TCP exposed to clients including GivTCP): **centi-amps (0.01 A)**. Computed independently by the FA inverter's secondary ARM (`FA_A2_03.bin`) by sampling PA0 (ADC1 channel 0) at 512-sample windows, computing true-RMS (sum-of-squares + integer sqrt + offset cal + 2.260 scale), then forwarding via USART1 (38400 baud) to the main ARM. Reports DC bus current (sum across all paralleled packs).

Both fields are in the same wire unit (0.01 A); they differ in scope. HR23 is per-primary-pack from the BMS; IR(51) is total DC bus current from the inverter's own sensor. For a 2-pack balanced setup, expect HR23 ~ IR(51) / 2.

#### FA inverter secondary ARM (`FA_A2_03.bin`)

17 KB application, loads at flash `0x08006400` (NOT `0x08000000` - the first ~25 KB of flash is reserved for a bootloader). Functions:

- 7-channel ADC scan triggered by TIM3, DMA1 Ch1 fed to ring buffer at SRAM `0x20003C3C`.
- PA0 = DC bus current (true-RMS, scale 2.260).
- PA1 = AC phase A (scale 0.483).
- PA5 = AC phase B (scale 0.398).
- USART1 (38400 baud) = inter-MCU bus to main ARM.
- CAN1 = secondary inter-MCU channel (purpose unclear, possibly higher-priority events).

### A316 / HY-series (ARMStore.bin)

Load address `0x08014000`. The CRC function is at `0x0801_7FCC`.

A316 contains **three** Modbus controller code paths on USART2:

1. **Device-`0x11` controller** (FC=3 of 38 regs at addr `0x00CA`, FC=6 at addr `0x00E7`) - energy meter / EMS path, not the BMS.
2. **5-device non-sequential rotation** (devices 1, 5, 6, 7, 8) doing FC=4 with count=2 over a 17-entry register-address table - purpose unclear, possibly HV expansion or parallel-inverter sense.
3. **LV-battery polling state machine at flash `0x08026C40`** (sole caller `0x08027440`). Despite being grouped here, this path does not use USART2: its requests go to the DSP over UART4 (see below). 4-state, 5-device 1..5 sequential rotation, FC=4 only, addr/count = 0x0000/21, 0x0015/19, 0x0028/20. Gated by 500-tick cadence counter at SRAM `0x200000DE`. Stages request frame at SRAM `0x2000070A + 0x84..+0x89`. RX parser at `0x08026EBC` reads response data from struct offset +6 (consistent with the BMS's non-standard FC=4 framing). Per-device decoded state at SRAM `0x200007A4 + (device_idx * 131)`.

**Path 3 is the LV battery path.** It produces the IR Block 1/2/3 polls Ken sees on his AC 3.0 wire captures.

**No FC=3 HR poll in the A316 ARM firmware.** The HR poll lives on the DSP (`DSPStore.bin`), confirmed in 2026-09. See below.

#### A316 BMS-channel polling expanded (audit, 2026-05)

A deeper look at A316's USART2 state machine identified the complete poll sequence. The relevant state machine at flash `0x0802B028` cycles through 17 different FC=4 IR windows (each 2 registers, device 1):

| State | IR Start | Regs read |
|---|---|---|
| 0 | `0x0010` | 16, 17 |
| 1 | `0x004E` | 78, 79 |
| 2 | `0x0052` | 82, 83 |
| 3 | `0x0092` | 146, 147 |
| 4 | `0x00D2` | 210, 211 |
| 5 | `0x0112` | 274, 275 |
| 6 | `0x0152` | 338, 339 |
| 7 | `0x0160` | 352, 353 |
| 8 | `0x0166` | 358, 359 |
| 9 | `0x0702` | 1794, 1795 |
| 10-14 | `0xFF00..0xFF06` | likely slice-select / system-status |
| 15-16 | `0x0524`, `0x0525` | 1316, 1317, 1318 |

The 17 windows above belong to path 2, whose purpose is still unclear. The LV battery path (path 3) uses the standard three blocks: an emulation of the A316 ARM firmware in 2026-09 queued Block 1 (device 1, start 0x0000, count 21), and a 66-hour wire capture from a G3 hybrid shows the three-block sweep plus the FC=3 HR poll (see [06-wire-captures.md](06-wire-captures.md#findings-from-a-66-hour-g3-capture)). An earlier version of this section said A316 does not poll the HR table at all and that an emulator needs all 17 windows. Both statements were wrong: the DSP polls the HR table. A316 also additionally uses FC=0x64 (GivEnergy proprietary) and USART4 ASCII Pylontech/PACE -- both out of scope for an HR-table-style emulator.

**TX path puzzle (solved in 2026-09)**: Path 3 has no USART2 byte emitter. The ARM copies the pending request (flag at `0x2000070A + 0x98`, fields at `+0x84..+0x8A`) into its UART4 frame to the DSP, and the DSP returns the BMS reply in its own UART4 frames, which set `+0x99`.

A316 is stricter about value validation than FA - filters cell voltages outside `(2200, 3700)` mV exclusive (silently drops, RAM keeps last value).

### A316: the DSP runs the BMS bus

Analysis in 2026-09 of A316 with its DSP image D316 shows that the ARM and DSP split the BMS work between them. The DSP is a TI C2000 part, most likely an F2806x.

- **Internal link.** The ARM and DSP exchange 45-byte frames over UART4 at 9600 baud, each with a Modbus CRC. The ARM puts its BMS read requests (device, FC, start, count) into these frames, and the DSP returns the raw Modbus replies in them.
- **The DSP is the Modbus master.** Every frame on the BMS bus comes from the DSP, and all of them are addressed to device 1:
  - The FC=3 HR poll every 240 ms: either HR0 to HR27 (start 0, count 28) or HR17 to HR25 only (start 17, count 9). The ARM chooses which.
  - The FC=4 IR reads that the ARM asks for, with the count capped at 26.
  - FC=6 writes to BMS registers 1 to 4, sent on counters between polls.
- **Reply check.** The DSP accepts a reply only when its length matches the function code and its CRC is correct.
- **Presence.** The first valid reply marks the BMS present and clears the comms fault. The DSP has no multi-reply debounce, unlike the 7-reply debounce reported for a Gen 1 inverter. Whether the ARM adds a delay of its own is not known yet.
- **BMS lost.** After about 30 seconds without a valid reply, the DSP zeroes the charge and discharge current limits and the SoC it holds, and sets the comms fault.
- **Current limits.** If HR13 (BMS firmware version) is 3011 or higher, the DSP takes the charge limit from HR26 and the discharge limit from HR27. Below 3011, it takes both from HR25. In one mode it raises both limits to at least 8.00 A.
- **SoC floor.** The DSP holds a SoC floor that defaults to 4%, the floor seen in wire captures. In one mode it clamps the BMS SoC to between the floor plus 1% and 99%.

These results come from firmware analysis and an emulation of the ARM side. They have not been confirmed on the wire yet.

## What's NOT done in the steady-state poll cycle

Confirmed across all surveyed firmwares and Ken's wire captures:

- **No FC=06 writes during normal polling** - read-only steady-state operation
- **No FC=10** (write multiple) builders found in any inverter firmware
- **No FC=23** (read/write multiple) builders found
- **No startup probe / handshake** - the inverter just begins polling device 1 with the standard HR query immediately after boot

## Implications for an emulator

The inverter-firmware analysis confirms what the BMS-firmware analysis and wire captures already established, with one practical addition: **stay inside the strictest validation envelope** to ensure the emulator works with any LV-compatible inverter, not just the specific one you tested against. See [07-emulator-implications.md](07-emulator-implications.md) for the full implementation guidance.
