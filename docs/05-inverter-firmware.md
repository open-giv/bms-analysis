# Inverter firmware analysis

The GivEnergy inverter firmware can also be statically analysed. The most important takeaway from this analysis is structural rather than detail-level:

> **The wire protocol is invariant across inverter variants. One BMS spec covers all compatible inverters.**

## The invariant

The same GivEnergy Gen 1 / Gen 2 LV BMS works with:

- AC 3.0 inverters
- Gen 1 Hybrid inverters
- Gen 2 Hybrid inverters
- Gen 3 Hybrid LV inverters (A316/D316)

Since the BMS firmware (`BMS_ARM.bin` v3017/3020/3022) implements one Modbus dialect, every compatible inverter must speak that same wire protocol or the BMS won't respond. **The wire protocol is the constant; inverter firmware variations are an internal-implementation concern that doesn't reach the wire.**

This means an emulator that satisfies the BMS-side spec (see [docs/01](01-protocol.md), [docs/02](02-holding-registers.md), [docs/03](03-input-registers.md)) will be accepted by any LV-compatible GivEnergy inverter.

## Inverter variants surveyed

Static analysis covered the ARM firmware for several variants:

| Variant | Firmware files | Architecture |
|---|---|---|
| FA-series ("PV String Inverter Gen3") | `FA_A1_xx.bin` (256 KB) + `FA_A2_xx.bin` (17 KB) + `FA_D1_xx.bin` | 3-MCU: ARM1 + ARM2 + DSP |
| A316 / Hybrid Gen 3 LV | `ARMStore.bin` (145 KB) + `DSPStore.bin` (131 KB) | ARM + DSP |
| A920/A921/A922 / Hybrid Gen 2 | `ARMStore.bin` (126 KB) + `DSPStore.bin` (131 KB) | ARM + DSP |
| A214/D212 / AC Coupled | `ARMStore.bin` (118 KB) + `DSPStore.bin` (131 KB) | ARM + DSP |
| AC 3.0 | (not identified separately) | unknown, but compatible with same BMS |

The labels in the first column follow the folders of the community firmware archive: "Hybrid Gen3 LV" (A316-D316, A318, A319), "Hybrid Gen2" (A920 to A922), "Hybrid Gen1" (A187), "AC Coupled" (A212, A214) and "PV String Inverter Gen3" (the FA packages). Earlier versions of this table called A316 a Gen 1/2 hybrid, FA the Gen 3 hybrid and A920 to A922 the AIO. Whether the FA firmware is used with LV batteries at all has not been rechecked.

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

**Path 3 is the LV battery path.** It asks for the same IR Block 1/2/3 polls that Ken's AC 3.0 captures show, and that the G3 captures in [06-wire-captures.md](06-wire-captures.md) show on a Hybrid Gen 3 LV.

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

The 17 windows above belong to path 2, whose purpose is still unclear. The LV battery path (path 3) uses the standard three blocks: an emulation of the A316 ARM firmware in 2026-09 queued Block 1 (device 1, start 0x0000, count 21), and a 90-hour wire capture from a G3 hybrid shows the three-block sweep plus the FC=3 HR poll (see [06-wire-captures.md](06-wire-captures.md#findings-from-a-90-hour-g3-capture)). An earlier version of this section said A316 does not poll the HR table at all and that an emulator needs all 17 windows. Both statements were wrong: the DSP polls the HR table. A316 also additionally uses FC=0x64 (GivEnergy proprietary) and USART4 ASCII Pylontech/PACE -- both out of scope for an HR-table-style emulator.

**TX path puzzle (solved in 2026-09)**: Path 3 has no USART2 byte emitter. The ARM copies the pending request (flag at `0x2000070A + 0x98`, fields at `+0x84..+0x8A`) into its UART4 frame to the DSP, and the DSP returns the BMS reply in its own UART4 frames, which set `+0x99`.

A316 is stricter about value validation than FA - filters cell voltages outside `(2200, 3700)` mV exclusive (silently drops, RAM keeps last value).

### A316: the DSP runs the BMS bus

Analysis in 2026-09 of A316 with its DSP image D316 shows that the ARM and DSP split the BMS work between them. The DSP is a TI C2000 part, most likely an F2806x.

- **Internal link.** The ARM and DSP exchange 45-byte frames over UART4 at 9600 baud, each with a Modbus CRC. The ARM puts its BMS read requests (device, FC, start, count) into these frames, and the DSP returns the raw Modbus replies in them.
- **The DSP is the Modbus master.** Every frame on the BMS bus comes from the DSP, and all of them are addressed to device 1:
  - The FC=3 HR poll every 240 ms: either HR0 to HR27 (start 0, count 28) or HR17 to HR25 only (start 17, count 9). The ARM chooses which (see the table below).
  - The FC=4 IR reads that the ARM asks for, with the count capped at 26.
  - FC=6 writes to BMS registers 1 to 4, sent on counters between polls. My 23-hour capture had none: the G3 sent only FC=3 and FC=4 (see [06-wire-captures.md](06-wire-captures.md#no-writes-to-the-battery)), so the conditions for these writes didn't occur in normal running.
- **Reply check.** The DSP accepts a reply only when its length matches the request and its CRC is correct. See [Reply acceptance](#reply-acceptance) below.
- **Presence.** The first valid reply marks the BMS present and clears the comms fault. The DSP reports this to the ARM. Neither chip has a multi-reply debounce, unlike the 7-reply debounce reported for a Gen 1 inverter. The only ARM code that reads the DSP's "valid reply seen" flag is a battery-type auto-judge routine (HR58 `enable_auto_judge_battery_type`) that nothing in the image calls, and that didn't run in emulation. If it ran, a battery loss would switch HR54 `battery_type` to lead acid, HR111 to 20% and the HR55 capacity to 125 Ah. An earlier version of this page read its writes as "battery connected" and "battery lost" events. They are HR111 values (50 and 20).
- **BMS lost.** The BMS link task runs every 40 ms and counts ticks since the last valid reply. After 750 ticks (about 30 seconds) it zeroes the charge and discharge current limits and the SoC it holds, and sets the comms fault. The next valid HR reply clears the fault.
- **Current limits.** If HR13 (BMS firmware version) is 3011 or higher, the DSP takes its charge limit from HR26 and its discharge limit from HR27. Below 3011, it takes both from HR25. During a battery calibration it raises both limits to at least 8.00 A. It scales the charge limit (HR26) down as the battery voltage falls from 48.0 V to 44.0 V, to 10% at 44.0 V and below, and the discharge limit (HR27) down as the voltage rises from 54.5 V to about 58.0 V, to 10% at 58.0 V and above. Both paths are also capped at the battery capacity times HR111 or HR112 (see HR11 below), and in the emulation at fixed ceilings of 60 A (charge) and 75 A (discharge) from the DSP variable `0xD400`. Those ceilings don't bind on my G3 (5 kW inverter, 3.6 kW battery rating): it charges at about 65 A and 3.49 kW by its own reading, and discharges at up to 70.7 A. So the 60/75 A values are either an artefact of the emulation's setup or depend on the model. After the taper the HR26 path never goes below 1.00 A and the HR27 path never below 2.00 A, so HR26 = 0 on its own still lets about 1 A through (see [HR26 = 0 is not a hard stop](#what-the-dsp-does-with-the-bms-status-registers)). The tapered limits become the positive (charge) and negative (discharge) bounds of the battery power. A capture from my G3 agrees: the charge current followed HR26 at the top of a charge (see the G3 LV note in [02-holding-registers.md](02-holding-registers.md)). An earlier version of this page read the tapers the other way round, because my table of the ARM's settings had the two cap values swapped.
- **Charge taper by SoC.** In BMS mode the DSP also limits the charge power by SoC: full rated power (3.6 kW on my inverter) up to 90% SoC, then down by 9.5% of it for each 1% of SoC, to 24% (864 W on mine) at 98% and above. This is the inverter's own taper seen in my capture, where the charge current fell from 60.5 A to 14.6 A between 90% and 98% SoC with both BMS limits at 80 A (24% of 60.5 A is 14.5 A). By the inverter's own reading the battery power was 2,497 W at 93% and 1,169 W at 97% on 29 September, where the formula gives 2,574 W and 1,206 W. A 3.6 kW G3 with a 9.5 kWh battery shows the same steps, 2.50, 2.16, 1.83, 1.50 and 1.17 kW from 93% to 97% (see [06-wire-captures.md](06-wire-captures.md#findings-from-a-90-hour-g3-capture)). During a calibration the step is 7.5% per 1% of SoC, down to 25% at 100%.
- **SoC floor.** The DSP holds a SoC floor that defaults to 4%, the floor seen in wire captures. The ARM sets it from HR110 (see the table below). After 5 s at or below the floor the DSP blocks discharge, until the SoC is back at the floor plus 4%. After 5 s at or below the floor minus 3% it forces a charge of at least 300 W, until the SoC is back at the floor plus 1%. A floor below 5% counts as 4% here, so with the default the forced charge starts at 1%. During a calibration it clamps the BMS SoC to between the floor plus 1% and 99%, and neither block runs.
- **Battery voltage.** The DSP measures the battery voltage itself. Its maximum and minimum are not fixed. The ARM sends them from the inverter settings HR98 `battery_high_voltage_protection_limit` (maximum) and HR97 `battery_low_voltage_protection_limit` (minimum), clamped to 54.0 V to 63.0 V and 20.0 V to 48.0 V. On my inverter HR98 is 58.5 V and HR97 is 43.2 V, so the maximum is 58.5 V and the minimum 43.2 V. The DSP's own defaults of 56.0 V and 42.0 V only hold from boot until the first settings frame from the ARM. During a battery calibration the maximum is raised by 5% (61.4 V on mine) and the minimum lowered to 86%. No charge voltage taken from BMS data was found. See [Battery voltage checks](#battery-voltage-checks) for the thresholds and what a trip does.

**Correction (27 September 2026).** An earlier version of this page said the DSP used a fixed maximum of 56.0 V and minimum of 42.0 V, which put the over-voltage trip at 57.0 V and 58.0 V. Those are only the boot defaults. On my inverter the trip is at 59.5 V and 60.5 V, from HR98 = 58.5 V. My capture agrees: the inverter read above 57.0 V for minutes during three top-ups, up to 57.61 V, and raised no fault.

#### Battery voltage checks

The DSP's control task runs every 20 ms. Its tick is a 1 ms timer interrupt and the task's period is 20 ticks. The BMS link task runs every 40 ms and sends a poll every 6th tick, which gives the 240 ms HR poll seen on the wire, so the 1 ms tick is confirmed two ways. Each voltage check below is one pass of the control task, so 50 checks are 1 s and 500 checks are 10 s.

With my settings (HR98 = 58.5 V, HR97 = 43.2 V):

| Check | Sets when | Clears when |
|---|---|---|
| Over-voltage | above the maximum + 1.0 V (59.5 V) for 50 checks (1 s), or above the maximum + 2.0 V (60.5 V) for 2 checks (40 ms) | 500 checks (10 s) below the maximum (58.5 V) |
| Under-voltage | below the minimum (43.2 V) for 5 checks (100 ms), unless the BMS is idle | 150 checks (3 s) above the minimum + 2.0 V (45.2 V) |
| Mismatch with HR22 | the DSP's reading and HR22 differ by more than 5.0 V for 5 checks, with the BMS active, HR22 non-zero and HR23 below 2.00 A. A reading below 24.0 V sets it at once | 500 checks (10 s) with the difference below 3.0 V |
| Start permit | HR22 above the minimum - 2.0 V (41.2 V) for 500 checks (10 s), with the BMS active. The converter only starts while this is given | at once, when HR22 is at or below that or the BMS is idle |

The over-voltage counts are totals, not runs in a row. Nothing resets a counter until it reaches its limit, so the rule is "more than 59.5 V for 1 s in total, or more than 60.5 V for 40 ms in total", counted since the last trip or since boot. The release counter also runs whenever the reading is below the maximum, so after a trip the fault can clear after anything from 20 ms to 10 s below 58.5 V. Outside BMS mode the maximum is a fixed 56.0 V.

The over-voltage check has no gate that I could find. It runs whether the battery is charging, discharging or idle, and it doesn't look at HR19, HR20, HR26 or SoC. Its only inputs are the DSP's own voltage reading, the maximum and BMS mode. The DSP's reading is what the inverter publishes as IR 50 `v_battery` (GivTCP's battery voltage), unchanged. On my system it reads 0.2 V to 0.3 V above HR22 at rest and about 1.3 V above it at 60 A.

What an over-voltage trip does:

- A fault check in the fast interrupt code switches the battery converter off, in both directions, while the fault is set. The power request goes to zero.
- It doesn't latch. When the fault clears, the converter soft-starts again by itself, at once if the DC bus is above 220 V and after 120 s if not.
- The inverter stays in its operating state and carries on without the battery. I found no relay write on this path.
- The DSP reports the fault to the ARM. During a calibration the ARM treats it as "battery full". Whether the app shows a fault outside a calibration is still open.

Because the maximum comes from HR98, anything that lowers HR98 lowers the trip. At HR98 = 56.0 V the trip is back at 57.0 V and 58.0 V.

#### Reply acceptance

I ran the DSP's own receive and parse code in Ghidra's emulator on real replies from my battery and on built ones:

- Byte 0 (the device) must be 0 to 15, and byte 1 (the FC) must be 3 or 4. Anything else is dropped. An FC=6 echo is ignored, so the DSP neither needs nor minds it.
- The expected length comes from the request, not from the reply's byte count field: `count * 2 + 5` bytes for FC=3, and `(count + 3) * 2` for FC=4 (the non-standard FC=4 framing). When that many bytes have arrived, the DSP checks the CRC. The FC=3 byte count field is never checked.
- A short reply never reaches the expected length and is dropped at the next poll. A long one fails the CRC. A valid reply followed by a stray byte is accepted.
- The DSP doesn't parse FC=4 replies. It copies them to the ARM, which parses them.

#### What the DSP does with the BMS status registers

These readers are from the D316 DSP image. The ones marked "run" I also ran in the emulator. Bits are 0-indexed.

| Register | What the DSP does |
|---|---|
| HR19 bits 0 and 1 | Both clear means the BMS is idle. The DSP then skips its BMS power limits, the "battery full" block and the voltage mismatch check. |
| HR19 bit 2 | In BMS mode, with bit 2 clear, the DSP raises the power request to at least 300 W of charge (run). My battery always sets it. It has no effect during a calibration. |
| HR19 bit 3 | Nothing. The DSP never reads it. My battery clears it at high cell voltage (see [02](02-holding-registers.md#register-19-bits)), and that has no effect on a G3. |
| HR19 bit 4 | Copied to a status bit sent to the ARM. |
| HR19 bit 5 | Outside a calibration, the DSP caps the power request at 120 W of discharge (run: requests of +200 W, 0 W and -50 W all become -120 W). So the inverter discharges a little. My battery pulses this bit at full charge, and in my capture each pulse started a discharge of about 2.8 A. |
| HR15 bit 0 | At 100% SoC it cancels the "battery full" block (which HR21 = 100% sets after 30 s, see below), and in forced charge it lets charging go past the upper SoC target. The DSP clears the bit whenever the previous SoC it received was below 100% (run), so below 100% it has no effect. |
| HR11 | Capped at 10000. After 50 full-poll replies (about 12 s after boot or after a battery loss), it replaces the capacity the DSP uses for the HR111/HR112 current caps (capacity x percentage + 1.5 A) and for the forced charge and discharge power. Before that, or with HR109 not 1, the DSP uses the installer's HR55. There is no table of battery models and no check against one. HR11 = 0 would drop the caps to 1.5 A and 2 A. It matters mainly with reduced HR111/HR112 or in forced charge and discharge. The ARM keeps its own copy and nothing else: the inverter's HR55 reads back as HR11 whenever HR11 is non-zero, and HR11 is never written to the inverter's EEPROM. |
| HR1 to HR4, HR10, HR12, HR16 to HR18 | Not stored. The reply parser skips them. |
| HR20 low byte | Sent to the ARM with every frame. |
| HR20 bit 2 | Outside a calibration, the charge power limit becomes zero at once (run), whatever the power request, including forced charge. After 30 s (1500 checks) the DSP also sets its "battery full" block, which clears 5 s after the cause is gone once SoC is below 99%. It raises no DSP fault. |
| HR20 bit 3 | The discharge power limit drops to 10% of rated power (360 W on a 3.6 kW inverter), in and out of a calibration (run). This is close to the 340 W that Ken saw when setting HR20 to 0x08 with `modbus_proxy` (see [02](02-holding-registers.md#inline-protocol-modification)). |

**HR26 = 0 is not a hard stop.** The HR26 path never goes below 1.00 A, so HR26 = 0 on its own leaves a charge bound of about 1 A (about 53 W at 53 V) until the "battery full" block sets after 30 s. HR20 bit 2 gives a zero charge limit at once. HR27 = 0 likewise leaves about 2 A of discharge. HR20 bit 3 alone leaves 360 W. The hard stop at empty is the SoC floor block.

**The "battery full" block also sets at 100% SoC.** In the D316 image the block's counter (in `FUN_003ECFB1`) counts each 20 ms check while any of these hold, with no BMS comms fault:

- the charge current limit taken from HR26 is 0
- HR21 is above 99
- HR20 bit 2 is set
- an internal flag (`0xD502` bit 0) that I haven't traced

At 1500 checks (30 s) it sets the block, which caps the power request at zero, and the counter starts again. The block clears only after 250 checks (5 s) with HR21 below 99, so it holds at 99%. HR15 bit 0 clears the block on every pass outside a calibration, which is how bit 0 at 100% lets charging go on. So with HR15 bit 0 clear, a G3 LV stops charging 30 s after HR21 reaches 100%, with HR26 and HR20 untouched. af987's captures show this. His solar charge in October stopped 30.2 s after HR21 read 100%, and his forced charge in September stopped 840.13 s (28 x 30 s) after HR21 read 100%, 19 s after the battery cleared HR15 bit 0 (see [06-wire-captures.md](06-wire-captures.md#two-nights-at-the-4-floor-and-a-solar-charge-to-100-3-to-5-october)). An earlier version of this page named only HR20 bit 2 as a cause of the block.

**HR20 bit 2 during a calibration.** While a battery calibration runs (HR29 non-zero), the DSP raises HR26 and HR27 to at least 8.00 A and never sets the "battery full" block. HR20 bit 2 then cuts the charge power limit to 5% of rated power instead of zero: 180 W on a 3.6 kW inverter, about 3.4 A at 53 V. That overrides the 8 A minimum, but it doesn't stop charging. On the ARM side, HR20 bit 3 and bit 2 (or a DSP over-voltage trip) are the "empty" and "full" end points of the calibration. I read the ARM part from the disassembly and didn't run it.

#### Inverter settings that change the BMS link

The ARM passes some of its own settings to the DSP in the UART4 frames. I traced each one from the DSP variable back to the inverter holding register that sets it, by emulating the ARM's frame builder and its register-write handler. The register names are from [givenergy-modbus](https://github.com/dewet22/givenergy-modbus).

| DSP behaviour | Inverter register |
|---|---|
| Full HR0 to HR27 poll (1) or short HR17 to HR25 poll (any other value) | HR109 `enable_bms_read`, 1 by default |
| SoC clamp to floor + 1% .. 99%, and both current limits held at 8.00 A or more | HR29 `battery_calibration_stage`, non-zero only while a battery calibration runs |
| SoC floor | HR110 `battery_soc_reserve`, replaced by a per-slot value inside timed slots |
| Cap on the charge limit (HR26) | HR111 `battery_charge_limit` |
| Cap on the discharge limit (HR27) | HR112 `battery_discharge_limit` |
| Battery maximum voltage, for the over-voltage check | HR98 `battery_high_voltage_protection_limit`, 58.5 V on my inverter |
| Battery minimum voltage, for the under-voltage check and start permit | HR97 `battery_low_voltage_protection_limit`, 43.2 V on my inverter |

I checked the HR111 and HR112 rows by running the DSP's limit code in Ghidra's emulator with one cap lowered at a time: each setting caps exactly one of the two limits. The DSP's forced charge and discharge code confirms which is which: it uses the HR111 value for a positive power limit that stops at the upper SoC target, and the HR112 value for a negative one that stops at the SoC floor. I also ran the ARM settings handler for the HR98 and HR97 rows: 58.5 V and 43.2 V give a maximum of 58.5 V and a minimum of 43.2 V, and 64.0 V and 50.0 V are clamped to 63.0 V and 48.0 V.

For a battery emulator, leave HR109 at 1. Otherwise the emulator also has to answer the short HR17 to HR25 poll, and there is a worse problem. The DSP only reads HR13 in the full poll. If it starts in short-poll mode, HR13 reads as 0 and it takes both limits from HR25. But if HR109 changes from 1 to anything else while the inverter runs, the DSP keeps the HR13 it saw and goes on reading HR26 and HR27 at their full-poll positions, past the end of the 23-byte short reply. In the emulator it got 368.86 A for charge (from the reply's CRC bytes) and 655.35 A for discharge (`0xFFFF` left over from an earlier full reply). That is a firmware fault, and HR109 = 1 avoids it.

These results come from firmware analysis and an emulation of the ARM side. A first capture from my G3 (September 2026) confirms the full HR poll every 240 ms and the IR sweep, and shows HR26 is the charge limit. Later captures (see [06-wire-captures.md](06-wire-captures.md)) also agree with the stop at the 4% SoC floor, the charge taper by SoC, and the small forced discharge on HR19 bit 5 at full charge. The HR111/HR112 caps, the voltage trips and the calibration behaviour have not been checked on the wire yet.

#### Note on the emulator used (27 September 2026)

I checked most figures on this page by running DSP code in Ghidra with the unofficial C28x processor module [outlandnish/ghidra-tms320c28x](https://github.com/outlandnish/ghidra-tms320c28x). That module had three instruction bugs, which I have reported upstream:

- `SUBF32` with an immediate operand subtracted the wrong way round ([#138](https://github.com/outlandnish/ghidra-tms320c28x/issues/138)). The taper figures above were already run with this one fixed.
- `ADDB AX,#8bit` zero-extended its constant where the C28x sign-extends it ([#142](https://github.com/outlandnish/ghidra-tms320c28x/issues/142)). The compiler writes `x - 2` as `ADDB AL,#-2`, so the emulated DSP ran every Modbus CRC over the wrong length and rejected every reply, and some thresholds read wrongly.
- `ADD loc16,#16bitSigned` set no flags ([#144](https://github.com/outlandnish/ghidra-tms320c28x/issues/144)). The DSP's float divide branches on those flags, so every division came out wrong.

With all three fixed, I re-checked the voltage checks, the current tapers, the battery-lost timeout and the ARM settings table. None of the figures published before changed. The SoC charge taper, the SoC floor blocks, the start permit and the reply acceptance could only be read correctly with the fixes, and are new here. The change to the voltage limits above came from a separate finding (the ARM's HR98/HR97 handler), not from the fixes.

## What's NOT done in the steady-state poll cycle

Confirmed across all surveyed firmwares and Ken's wire captures:

- **No FC=06 writes during normal polling** - read-only steady-state operation. My G3 LV sent none in any of my captures, 26 to 29 September 2026.
- **No FC=10** (write multiple) builders found in any inverter firmware
- **No FC=23** (read/write multiple) builders found
- **No startup probe / handshake** - the inverter just begins polling device 1 with the standard HR query immediately after boot

## Implications for an emulator

The inverter-firmware analysis confirms what the BMS-firmware analysis and wire captures already established, with one practical addition: **stay inside the strictest validation envelope** to ensure the emulator works with any LV-compatible inverter, not just the specific one you tested against. See [07-emulator-implications.md](07-emulator-implications.md) for the full implementation guidance.
