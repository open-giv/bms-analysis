# Wire captures

This document covers the methodology for capturing RS485 traffic between the inverter and the BMS, and the timing / cadence findings from analysing those captures.

## Hardware

A USB-RS485 dongle in monitor (passive listen) mode is sufficient. Tested with:

- [Waveshare USB to RS485](https://www.waveshare.com/usb-to-rs485.htm) (~GBP 10-15, isolated, recommended)

Any FT232R or CH340-based USB-RS485 dongle works. Cheaper CP2102+SP485E modules can have signal-integrity issues - the Waveshare with a proper isolated transceiver is more reliable.

### Tap point

RS485 is a multidrop bus, so adding a passive listener doesn't disturb existing communication. The cleanest tap is at the inverter's BMS terminal block:

- Take an Ethernet cable
- Land its A and B wires (typically pins 4 & 5, or 1 & 2 on the BMS RJ45) on a spare position in the inverter's BMS terminal block (alongside the existing battery cable)
- Connect the dongle's A/B inputs to the same wires
- (Optional) Connect ground reference if the dongle has one

The dongle will see all traffic on the bus without interfering. No splicing of the existing cable is required.

## Capture software

The tool [`tools/serial_hexdump_logger.c`](../tools/serial_hexdump_logger.c) (originally by @kenbell) logs all incoming RS485 bytes with timestamps to a file. Output format:

```
2026-05-01 07:23:39.416Z  00000000  01 03 00 00 00 1C 44 03                          |......D.|
2026-05-01 07:23:39.516Z  00000008  01 03 38 00 65 FF FF FF FF FF FF FF FF XX XX XX  |..8.e...........|
2026-05-01 07:23:39.516Z  00000018  XX XX XX XX XX XX XX FF FF 00 BA 00 30 0B CE 00  |..............0.|
...
```

(Serial bytes redacted with `XX` placeholders. In a real capture, bytes 13-22 of the HR response carry the BMS serial as ASCII.)

Each line shows: timestamp, byte offset into the capture stream, up to 16 hex bytes, and the ASCII rendering of those bytes.

Timestamps are UTC, marked with a trailing `Z`. Logs from older versions of the logger are in the logging machine's local time with no zone marker. `tools/join_streams.py` refuses those unless you pass `--wire-tz` (e.g. `--wire-tz Europe/London`), because joining local time against `tcp_poller.py`'s UTC timestamps shifts every TCP value by the UTC offset.

The log file argument can be a `strftime` template, e.g. `captures/%Y-%m-%d/wire.log`. The logger expands it with the UTC date of each read, and switches to a new file, creating its directory, when the UTC date changes. A plain file name works as before.

Timestamps are when the logger flushed - lines sharing a timestamp are bytes received in the same flush, typically belonging to one Modbus frame. **Note**: occasionally the logger splits a frame across two flushes ~1 ms apart; a parser must handle this (see [`tools/parse_log.py`](../tools/parse_log.py) for a robust approach).

## Parsing the captures

[`tools/parse_log.py`](../tools/parse_log.py) reads `serial_hexdump_logger` output and reassembles complete Modbus frames using FC-determined length, validates the CRC of each frame, and produces structured output (per-frame role, device, FC, latency, etc.).

The parser correctly handles:

- The non-standard FC=4 response format (length implicit from the matching request's count, not from a byte_count field)
- Multi-flush frames (concatenated by content, not just timestamps)
- Out-of-sync recovery (skips malformed bytes, retries decode)
- Request -> response pairing (matches each response to the immediately preceding request)

Run on a logger output file:

```bash
python3 tools/parse_log.py path/to/logger_output.log
```

## Findings from a 3.4-minute cold-start capture

The reference capture (`cold_start.log` from @kenbell) was a 3.4-minute window starting in the middle of normal inverter operation, not from inverter cold boot - meaning the HR poll loop was already running when capture started.

### Frame totals

| Metric | Value |
|---:|---|
| Total bytes captured | 58,319 |
| Modbus frames decoded | 1,698 |
| Bytes dropped during resync | 0 (clean bus) |
| Capture span | 203.6 seconds |

### Cadence by query type

| Query | Count | Avg gap | Min | Max |
|---|---:|---:|---:|---:|
| Device 1 HR poll (FC=3, start=0, count=28) | 831 | 245.2 ms | 231 ms | 481 ms |
| Device N IR Block 1 (FC=4, start=0, count=21) | 9 (= 1x 5 devices + duplicates) | ~10 s | - | - |
| Device N IR Block 2 (FC=4, start=0x15, count=19) | 5 | ~10 s | - | - |
| Device N IR Block 3 (FC=4, start=0x28, count=20) | 4 | ~10 s | - | - |

The HR poll dominates. IR queries are interleaved opportunistically.

### BMS turnaround latencies (request -> response gap)

| Query | n | mean | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|---:|
| Device 1 HR poll | 831 | 101 ms | 101 | 103 | 90 | 114 |
| Device N IR Block 1 | 6 | 87 ms | 88 | 89 | 87 | 89 |
| Device N IR Block 2 | 5 | 84 ms | 84 | 84 | 83 | 84 |
| Device N IR Block 3 | 4 | 86 ms | 86 | 87 | 84 | 87 |

The HR turnaround is ~17 ms longer than IR because the HR response is larger (61 bytes vs 47 / 43 / 45) and that takes longer to TX at 9600 baud.

**Practical emulator latency budget**: respond within ~100 ms of a request to look like a real BMS. This is generous for a Pi or ESP32 implementation.

### Boot-sequence shape

The capture starts mid-stream with the HR poll loop already running. Observed:

- **First 12 seconds**: HR poll only, every ~250 ms to device 1
- **+12s onwards**: First IR poll fires (device 1 Block 1)
- **+12s to +180s (cycle complete)**: Full 5-device IR sweep across all 3 blocks, ~10s spacing
- **HR poll never pauses** throughout

There's no special boot probe or handshake - the inverter just immediately begins polling device 1 after it sees the BMS is responsive.

### "Absent device" pattern

Ken's setup has 2 batteries (devices 1 and 2). The inverter still polls devices 3, 4, 5 - and gets back specific empty-but-valid responses. See [03-input-registers.md](03-input-registers.md) for the byte-level pattern.

## Findings from a 90-hour G3 capture

@af987 captured a GivEnergy G3 Hybrid 3.6 kW inverter with one 9.5 kWh battery (PR #14). The capture ran from 21 to 25 August 2026, about 90 hours, and covers 1.35 million request and response pairs. It includes the RS485 wire stream and a 1 Hz `tcp_poller.py` stream from the same system, joined with `tools/join_streams.py`.

### Timestamp alignment

The wire timestamps in this capture are local time (BST) that was labelled as UTC, so the wire stream is one hour ahead of the TCP stream. Before the correction, HR23 and the inverter's reported battery current correlate at 0.47. After moving the wire stream back one hour, they correlate at 0.997, with a median difference of 0.17 A. All the results below use the corrected alignment. The logger now writes UTC, and `join_streams.py --wire-tz` handles older logs, so new captures don't have this problem.

### Poll cadence on a G3

| Query | Interval per device |
|---|---|
| HR poll (device 1, FC=3, start 0, count 28) | 240 ms, with occasional gaps of 480 ms |
| IR Block 1 (FC=4, start 0x0000, count 21) | twice, 10 s apart, every 200 s (100 s on average) |
| IR Block 2 (FC=4, start 0x0015, count 19) | about 200 s |
| IR Block 3 (FC=4, start 0x0028, count 20) | about 200 s |

The inverter sends one FC=4 request about every 10 s, in the order Block 1, Block 1, Block 2, Block 3 for each device in turn. It polls devices 2 to 5 as well, and with one battery fitted those slots return the absent-device pattern.

### The inverter reports the BMS values unchanged

Every battery value that the inverter publishes over Modbus TCP matches a value on the wire exactly, once you allow for a delay of 10 to 20 seconds between the wire read and the TCP value:

| TCP field (`tcp_poller.py`) | Wire source | Match after 20 s |
|---|---|---|
| `soc` | IR Block 2, SoC byte | 100% |
| `num_cycles` | IR Block 2, cycle count | 100% |
| `cap_remaining` | IR Block 2, remaining capacity | 100% |
| `v_cell_01` to `v_cell_16` | IR Block 3, cell voltages | 100% |
| `t_max` | IR Block 3, offset 32 (0.1 °C) | 100% |
| `t_min` | IR Block 3, offset 34 (0.1 °C) | 100% |
| battery current (`p_battery / v_battery`) | HR23 (0.01 A) | correlation 0.997 |

So an emulator controls what the inverter and the GivEnergy app show by setting these registers. The five temperatures in IR Block 1 are separate sensors. They don't feed `t_max` or `t_min`.

### Values that stayed fixed

- **HR11** stayed at 186 for the whole capture while SoC moved between 4% and 95%. 186 Ah at 51.2 V is 9.5 kWh, the size of this battery. HR11 behaves as the capacity of the batteries online, not the remaining charge. See [02-holding-registers.md](02-holding-registers.md).
- **HR25** stayed at 15000 (150 A).
- The inverter's charge and discharge limits over TCP changed once, from 38% to 50%, at 22:55 UTC on 23 August. The BMS registers didn't change at that moment, so the change came from an inverter setting.
- The largest currents were 65 A charging and 76 A discharging. On a 3.6 kW inverter these fit the inverter's own power limit.

### Discharge stops at the 4% SoC floor

GivEnergy inverters stop discharging at 4% SoC. Discharge stopped at the floor three times in this capture, at 18:20 UTC on 21 August, at 07:21 UTC on 23 August and at 17:59 UTC on 24 August. The last SoC readings from IR Block 2 before the first and last stops were 9, 7, 5% and 10, 8, 5%, falling about 2% per 200 s reading. HR21 shows the moment: it first read 4% at 18:19:56, 07:21:29 and 17:58:57, and the current was below 0.5 A 5.3 s, 2.4 s and 0.5 s later. (An earlier version of this paragraph missed the stop on 23 August, at about 63 A, and gave the delays as 6 s and 1 s.) The G3 LV DSP takes its SoC from HR21 and blocks discharge after 5 s at or below the floor (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)), which fits.

At the same poll that the current dropped to zero, HR19 bit 3 (0-indexed) started switching between set and clear on almost every poll. HR19 moved between 206 and 198, or between 207 and 199. The lowest cell was then between 2951 and 3039 mV. The switching continued until the battery next charged, about 8 hours later on 21 August and about 70 minutes later on 24 August. It didn't happen at the stop on 23 August, where the lowest cell read 3.167 V at rest 35 s after the stop. During normal discharge HR19 was always 207. The switching started after the stop, not before, so it doesn't look like the reason the inverter stopped. It matches Ken's note that bit 4 (1-indexed) "oscillates below 4% SOC".

### The 9.5 kWh battery's registers and limits

@af987 contributed this capture in PR #14, and the joined file is in the repo as [captures/G3_HY_3_6_G3_9_5/joined.parquet.redacted](../captures/G3_HY_3_6_G3_9_5/). Its timestamps are already corrected: HR23 against the inverter's battery current gives a correlation of 0.998, or 0.999 with a 2 s lag. It now has all of HR0 to HR27, so the earlier gap (no HR20 to HR27) is closed. These are the values I found in it. The battery is a Gen 3 9.5 kWh, which is a different BMS firmware family from mine.

| Field | Value in this capture |
|---|---|
| HR13 and IR Block 2 firmware version | 4009 |
| HR11 | 186 throughout |
| HR12 | 48 throughout, the same as my 3020 battery |
| IR Block 2 capacities | total (calibrated) 200.00 Ah, design 186.00 Ah, remaining 7.48 Ah to 191.72 Ah; 464 to 468 cycles |
| HR25 | 150.00 A throughout |
| HR14, HR16, HR20 | 0 throughout. HR20 never set a bit, but the battery never reached 100% either |
| HR15 | 1 in 95% of polls, both charging and discharging. It was 0 for three stretches of about 10 minutes and one of about 4 hours on 24 August, and for one minute on 22 August |
| HR17, HR18 | HR17 changed 322,727 times in 323,009 s. HR18 changed only at about 02:05 and 04:05 UTC each day. Both fit the clock hash in [02-holding-registers.md](02-holding-registers.md) |
| HR19 | `0xCF` and `0xCE` in normal running, `0xC6` and `0xC7` at the SoC floor (see above), plus 13 polls of `0xC5` or `0xCD` at zero current |
| HR21 | 4% to 97% |
| HR22 | 47.40 V to 54.82 V |

**HR26 (charge limit)** was 100.00 A except once. At 14:55:51 UTC on 23 August, at 91% SoC, it dropped to 66.00 A. The pack was at 54.76 V and charging at about 51.6 A, and the last cell reading was 3.403 V to 3.413 V. The cut didn't bind, because the current was already below 66 A. From 92% the current followed the inverter's own SoC taper instead: by the inverter's reading the battery power was 2.80 kW up to 92%, then 2.50, 2.16, 1.83, 1.50 and 1.17 kW at 93% to 97%. Those are the same steps as on my G3 (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)). Charging stopped at 97% at 15:00:20, and HR26 came back in 10 A steps about every 11 s: 76, 86, 96 and then 100 A by 15:01:40.

**HR27 (discharge limit)** was 120.00 A normally. Near the bottom of a discharge the BMS cut it in two steps:

| When (UTC) | HR27 | SoC | HR22 | Discharge current | Lowest cell, last reading |
|---|---|---|---|---|---|
| 21 Aug 18:12:56 | 100 A | 8% | 49.88 V | about 55 to 59 A | 3.141 V |
| 21 Aug 18:16:53 | 60 A | 6% | 48.63 V | about 59 to 60 A | 3.063 V |
| 23 Aug 05:45:06 | 100 A | 6% | 49.82 V | about 68 A | 3.177 V |
| 24 Aug 17:51:27 | 100 A | 10% | 49.79 V | about 67 A | 3.115 V |
| 24 Aug 17:56:22 | 60 A | 8% | 48.57 V | 67 A, then about 63 A | 3.019 V |
| 25 Aug 02:37:59 | 100 A | 4% | 50.26 V | at rest (0.2 A) | 3.101 V |
| 25 Aug 05:44:44 | 100 A | 6% | 49.94 V | about 63 to 68 A | 3.142 V |

The 100 A steps came at about 49.8 V to 49.9 V under load (once at rest, at 4% and 50.26 V) and the 60 A steps at about 48.6 V. IR Block 3 is only read every 200 s, so the cell values can be up to 200 s old. HR27 stayed cut until the battery next charged, then came back in 10 A steps about every 11 s, the same release pattern as HR26 on my battery.

**At the floor this battery goes deeper than mine.** At the stop on 24 August, still under about 63 A, the lowest cell read 2.951 V. My 3020 battery's lowest cell was 3.087 V at 5% under 71 A, and 3.167 V at 5% on a slow discharge (see [below](#discharge-to-the-reserve-and-a-full-charge-27-29-september)).

### A battery start, a charge to 100% and the reserve (30 September to 1 October)

@af987 contributed a second capture from the same G3 HY 3.6 kW and 9.5 kWh battery (BMS firmware 4009) in PR #32: [captures/G3_HY_3_6_G3_9_5/cold_start_full_cycle.joined.parquet.redacted](../captures/G3_HY_3_6_G3_9_5/). It runs from 10:55 UTC on 30 September to 15:23 UTC on 1 October 2026, with 425,474 request and response pairs joined with GivTCP. HR23 and the inverter's battery power correlate at 0.996 over every HR poll, with no time shift. His capture covers two starts, a solar charge, a forced charge to 100%, a forced discharge for the evening export, the night, a forced discharge to 4% and a short dwell there, and a recharge. All times below are UTC (BST is one hour ahead).

Every HR poll in the capture was FC=3, device 1, start 0, count 28. There was no short HR17 to HR25 poll, so HR109 was 1, and there was no FC=6 write. HR26 stayed at 100.00 A, HR25 at 150.00 A and HR11 at 186 throughout.

The joined file only keeps requests that got a reply, so it can't show how long the inverter polled before the battery first answered.

**The battery's start.** The capture has two starts, at 10:55:03.738 and at 11:20:33.556, 377 s after the previous reply. In both, the inverter was already running. Its first IR reply came about 4 s after the first HR reply and was partway through its usual sweep (device 4 Block 2, and device 2 Block 3), and GivTCP was already reading the inverter, with 6.6 V at its battery terminals. So each start is the battery's own start, and the battery went through the same steps at the same offsets from its first reply, to within 20 ms:

| After the first reply | What the wire shows |
|---|---|
| 0 s | HR19 = `0x0E`, HR20 = `0x04` (bit 2, over-voltage) at 23% or 27% SoC and about 52.7 V, HR23 = +0.01 A, HR15 = 0. HR26, HR27, HR21 and HR22 have their normal values. The inverter reads 6.6 to 6.7 V |
| 7.6 s | HR19 = `0x8F`. HR23 reads -3.9 A or -4.3 A, falling to near 0 over about 3 s, as the battery charges the inverter's input capacitors |
| about 10 to 11.5 s | The inverter's own battery voltage (GivTCP) reaches 49 to 53 V |
| 11.7 s | HR19 = `0xCF`, its normal discharge and idle value |
| 12.2 s | HR20 = 0 |
| 22.5 s and 22.75 s | First charge current, 10.5 A and 9.1 A |
| 624 s | HR15 goes from 0 to 1 |

So HR19 bits 6 and 7 (0-indexed) are clear while the battery's output is off, and HR20 bit 2 is set at start-up with no over-voltage. Every earlier G3 capture had bits 6 and 7 set all the time. The G3 LV DSP doesn't read bits 6 and 7 (see [05-inverter-firmware.md](05-inverter-firmware.md#what-the-dsp-does-with-the-bms-status-registers)). The charge started about 10 s after the inverter's own voltage reading came up to the pack voltage. That fits the DSP's voltage mismatch check and its start permit, which both wait 10 s (see [05-inverter-firmware.md](05-inverter-firmware.md#battery-voltage-checks)), though GivTCP's 1.4 s sampling makes the timing rough. Device 1's first IR replies came 64 s and 134 s after the start, when the sweep reached device 1, and their values were normal. GivTCP showed the battery's SoC, cycles and remaining capacity from before each start until device 1's Block 2 was read again, 92 s and 162 s after the start.

**The top of the charge.** The forced charge started at 14:11:32 at HR21 = 73%. The current held at about 64.3 A by HR23 (3.49 kW by the inverter) up to 90%, and then followed the inverter's own SoC taper. HR26 and HR27 never moved:

| HR21 | Reached at | HR23 | Inverter's battery power |
|---|---|---|---|
| 90% | 14:42:29 | 64.1 A | 3.49 kW |
| 91% | 14:44:22 | 58.2 A | 3.16 kW |
| 93% | 14:48:43 | 46.4 A | 2.50 kW |
| 95% | 14:54:16 | 34.4 A | 1.83 kW |
| 97% | 15:02:00 | 22.2 A | 1.17 kW |
| 98% | 15:07:25 | 16.05 A | 0.84 kW |
| 100% | 15:16:54 | 16.0 A | 0.84 kW |

From 98% the current stayed at the DSP's floor of 24% of rated power. HR21 read 100% from 15:16:54, and the pack was still drawing 16.0 A (about 0.87 kW by HR23 and HR22, and 0.84 kW by the inverter). The highest cell was 3.385 V to 3.404 V. The highest cell in the whole capture was 3.418 V, at 90% and 64 A, and HR22 peaked at 54.73 V. HR20 stayed 0, and HR19 stayed `0xCE`, with no bit 5 and no bit 3 clear.

The battery kept HR15 bit 0 set at HR21 = 100% (3,340 of 3,416 polls). It had been set since 75% on the way up. On a G3 that cancels the DSP's "battery full" block and lets a forced charge go past its upper SoC target (see [05-inverter-firmware.md](05-inverter-firmware.md#what-the-dsp-does-with-the-bms-status-registers)). The inverter kept charging at 16 A for 14 minutes at 100%. HR15 bit 0 cleared at 15:30:35.392, while the pack was still charging at 16 A. The current fell from 15.99 A to 0.18 A at 15:30:54.349, 19 s later. That is 840.13 s after HR21 first read 100% (15:16:54.221), which is exactly 28 periods of the 30 s count of the DSP's "battery full" block. If that counter runs whenever HR21 is above 99, with HR15 bit 0 cancelling the block each time it sets, the block would take hold at the first 30 s mark after the bit cleared, which is when the current fell. af987 said on #32 that he stopped the forced charge by hand at about 16:30 BST (15:30 UTC). The wire can't show when his command reached the inverter, so the capture doesn't settle whether his stop or the DSP's block ended the charge: both fit within the same minute. What it does show is that HR15 bit 0 cleared 19 s before the current changed, so the bit didn't clear because charging had stopped. His October capture shows the same block ending a solar charge 30 s after 100% (see [below](#two-nights-at-the-4-floor-and-a-solar-charge-to-100-3-to-5-october)). His app showed about 1.2 kW at the time, but the wire showed 16 A (about 0.84 kW).

HR21 runs ahead of the IR Block 2 SoC on the way up. HR21 read 100% while Block 2 read 97% (193.49 Ah of 200.00 Ah), and Block 2 peaked at 99%. In 510 Block 2 reads, HR21 was 0 to 3 points above the Block 2 SoC and never below it, with the gap opening while charging above about 40% and closing during discharge. The inverter's taper and its 100% follow HR21. GivTCP's battery SoC comes from Block 2 and never showed 100%.

This is very different from my Gen 1 battery (3020), which cuts HR26 from 80 A to 32, 8 and 3.2 A at 98% as its highest cell passes about 3.47 to 3.50 V, sets HR19 bit 5 and HR20 bit 2 at 100%, sends HR15 = 0 at 100%, and only releases HR26 when the highest cell falls below about 3.40 V (see [below](#a-solar-charge-to-full-and-the-trickle-release-29-september)). af987's 9.5 kWh battery left the whole taper to the inverter, and its cells were still on the flat part of the LFP curve at 100%. The HR26 cut to 66 A seen once in his August capture, at 91% with the highest cell at about 3.41 V, didn't happen here, even with a cell at 3.418 V under 64 A.

**The reserve.** The forced discharge to the reserve started at 07:47:21 on 1 October and ran at 66 to 69 A. HR27 stepped down twice:

| When | HR27 | HR21 | HR22 | HR23 | Lowest cell, last reading |
|---|---|---|---|---|---|
| 08:00:59 | 100 A | 11% | 49.79 V | -68.1 A | 3.109 V |
| 08:10:32 | 60 A | 5% | 48.42 V | -69.4 A | 3.033 V |

The 100 A step came at the same voltage under load as in August, and the 60 A step slightly lower (48.42 V against 48.57 V to 48.63 V). The 60 A step cut the current to about 62.7 A, as on 24 August. HR21 first read 4% at 08:12:07.733, and the current was zero 0.48 s later, faster than the DSP's 5 s floor check. A forced discharge stops at the floor in its own code (see [05-inverter-firmware.md](05-inverter-firmware.md#inverter-settings-that-change-the-bms-link)), which may explain it.

The battery then sat at 4% from 08:12:08 to 08:27:13, about 15 minutes, with HR23 between -0.10 A and +0.46 A. HR21 and the Block 2 SoC both read 4%, with 8.98 Ah remaining. At rest the pack recovered from 47.94 V to 49.94 V, and the cells to 3.064 V to 3.081 V (lowest) and 3.102 V to 3.117 V (highest). HR19 only toggled bit 0 with the sign of the current. Bit 3 didn't clear, which fits the August finding that it clears only at rest with a cell at or below about 3.04 V. A forced charge at 64.4 A started at 08:27:13, and HR21 went to 5% half a second later. HR27 stayed at 60 A for 38 s, then came back by 10 A every 11.04 s, from 70 A at 08:27:51 to 120 A at 08:28:46.

HR15 was 1 in 57% of polls in this capture. Apart from the 624 s after each start, it was 0 for 223 s at the start of the forced charge, for 11.6 hours from 15:30:35 to 03:06:04 (the evening discharge and the night, until the next charge started), and for about 12 and 4 minutes during the charge on 1 October. Its meaning on 4009 is still open.

### Two nights at the 4% floor and a solar charge to 100% (3 to 5 October)

@af987 contributed a third capture from the same G3 HY 3.6 kW and 9.5 kWh battery (BMS firmware 4009) in PR #36: [captures/G3_HY_3_6_G3_9_5/SOCdwell.joined.parquet.redacted](../captures/G3_HY_3_6_G3_9_5/). It runs from 15:50 UTC on 3 October to 08:47 UTC on 5 October 2026, with 599,611 HR polls and 14,710 IR polls joined with GivTCP, and no time shift. He set it up to show two dwells at the 4% floor and one at 100%, and the 100% came from solar, not a forced charge. All times below are UTC.

The inverter sent only FC=3 and FC=4. HR11 stayed at 186, HR20 at 0, HR25 at 150.00 A and HR26 at 100.00 A throughout. HR19 only took `0xCF` and `0xCE`, plus 27 polls of `0xCD` at exactly 0 A, so bit 5 never set and bit 3 never cleared.

**The taper on a solar charge.** The inverter's SoC taper (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)) applied to the solar charge too. Up to 93% the sun gave less than the taper allowed. From 94% the charge followed the taper:

| HR21 | Reached at | HR23 | Inverter's battery power |
|---|---|---|---|
| 94% | 14:04:15 | 40.2 A | 2.17 kW |
| 95% | 14:07:14 | 34.2 A | 1.83 kW |
| 96% | 14:10:45 | 28.2 A | 1.50 kW |
| 97% | 14:15:01 | 22.1 A | 1.17 kW |
| 98% | 14:20:27 | 16.0 A | 0.84 kW |
| 100% | 14:49:58 | 15.75 A | 0.84 kW |

These are the same steps as in his forced charge. The inverter's reading is 0.97 of 3.6 kW x (1 - 0.095 x (SoC - 90)) at every step.

**The stop at 100%.** HR21 first read 100% at 14:49:58.321. HR15 had been 1 since that morning, and it cleared 1.2 s later, at 14:49:59.521, while the pack was still charging at 15.76 A. The current held at 15.7 A until 14:50:25.676, and at 14:50:28.556 it was -0.54 A. After a dip to -2.71 A it was back within 0.05 A of zero by 14:50:34.8. So the charge stopped 30.2 s after HR21 reached 100%, with HR26 at 100 A, HR20 at 0 and no HR19 bit 5. That is the G3 DSP's "battery full" block, which HR21 above 99 sets after 30 s unless HR15 bit 0 is set (see [05-inverter-firmware.md](05-inverter-firmware.md#what-the-dsp-does-with-the-bms-status-registers)). So the difference from his forced charge in September, which ran on at 16 A at 100%, is HR15 bit 0, not the kind of charge.

The highest cell in the whole capture was 3.446 V, at 99% and 15.8 A, with the lowest cell at 3.421 V. The spread had been 3 mV at rest at 87% and about 10 mV through the bulk charge. HR22 peaked at 55.18 V. IR Block 2 never passed 99% (198.93 Ah of 200.00 Ah), and nor did GivTCP's SoC.

**The dwell at 100%.** The battery sat at 100% from 14:50:35 until the forced export started at 16:01:06, about 70 minutes:

- HR21 read 100% in all 17,211 polls, and Block 2 read 99%.
- HR23 stayed between -0.12 A and +0.12 A, and 99.9% of polls were within 0.10 A. The net charge was zero.
- There was no top-up. HR21 never fell below 100%, and the DSP's block only clears after 5 s below 99%.
- HR15 stayed 0, and HR19 only changed bit 0 with the sign of the near-zero current.
- HR22 relaxed from 54.97 V to 53.57 V. The cells relaxed to 3.341 V and 3.336 V, and the spread fell to 5 mV. Nothing on the wire marked balancing, and the fall in spread fits the cells relaxing after the current stopped.

So at full this battery left the stop to the inverter. My 3020 battery cuts HR26 to 3.2 A and pulses HR19 bit 5 at 100% (see [below](#a-solar-charge-to-full-and-the-trickle-release-29-september)). His sent nothing, and the G3 held the pack at zero current.

When the export started at about 50 A, HR21 stayed at 100% for 6.8 minutes, then stepped to 99% at 16:07:55, 98% at 16:09:55 and 97% 13 s later, to meet Block 2.

**The two dwells at the floor.** The first came after the night's house load and the second after the evening export and house load. Both ended at the forced charge at 01:30 (02:30 BST).

| | 4 October | 4 to 5 October |
|---|---|---|
| HR27 120 to 100 A | 00:04:41, 5%, 50.17 V, -3.0 A | 20:10:33, 5%, 50.14 V, -7.3 A |
| Lowest cell, last reading | 3.101 V | 3.102 V |
| HR21 first reads 4% | 00:20:09.433 | 20:19:58.622 |
| Current below 0.5 A | 4.32 s later | 4.80 s later |
| Dwell | 1 h 10 min | 5 h 10 min |
| HR23 during the dwell | -0.15 to +0.17 A | -0.21 to +0.14 A |
| Cells at rest (lowest to highest) | 3.100 to 3.132 V | 3.101 to 3.135 V |
| Block 2 remaining | 8.99 to 8.95 Ah | 8.98 to 8.63 Ah |
| HR15 goes from 0 to 1 | 3605.5 s after the stop | 3605.3 s after the stop |
| HR27 back to 110 A | 202 s after the charge started | 7.7 s after the charge started |

- **HR27.** Under 3 to 7 A only the 100 A step happened, at about 50.15 V. The pack never fell to the 48.4 to 48.6 V of the 60 A steps under load (its lowest was 49.76 V). Both steps came with the last lowest-cell reading, about 40 s old, at 3.101 to 3.102 V. The one step at rest in August also had a lowest cell of 3.101 V.
- **The stop.** Both stops came within the DSP's 5 s floor check, at 4.3 s and 4.8 s. Neither was a forced discharge.
- **During the dwell.** No poll in either dwell reached 0.3 A, so the inverter made no charge or discharge pulses. HR21 and the Block 2 SoC both stayed at 4%. Block 2's remaining capacity fell by 0.01 Ah every 7 to 10 minutes, with no step or recalibration. The lowest cell recovered by about 20 mV, and on the long night it then held at about 3.10 V. HR19 bit 3 stayed set, because the lowest cell stayed above 3.08 V, and HR20 stayed 0.
- **HR15.** Both times HR15 bit 0 went from 0 to 1 one hour and about 5 s after the current stopped, with nothing else changing on the wire. It then stayed 1 through the next charge and discharge until HR21 reached 100% (apart from 2 minutes at 89% on the way up). In the 70 minutes at rest at 100% it stayed 0.
- **The end.** The forced charge went straight to about 64.2 A. HR27 came back from 100 A in two steps of 10 A, 11.04 s apart, as before. The delay after the charge started was 202 s on the first night and 7.7 s on the second (38 s in his September capture), and I don't know what sets it.

**Pack current in IR Block 2.** This capture was decoded with the corrected `decode_fields.py`, which reads Block 2 offsets 9 to 12 as a signed 32-bit current in mA (see [03-input-registers.md](03-input-registers.md#block-2-regs-0x0015---0x0027-19-regs)). Across 736 Block 2 reads it agrees with HR23. The correlation is 0.9995, and the median difference from the nearer HR poll is 18 mA. The sign agreed in every read with more than 1 A flowing. 38 reads were below -65.536 A, down to -79.012 A, and all of them matched HR23.

## Findings from my G3 capture (September 2026)

I captured my own Hybrid Gen3 LV (firmware D0.316-A0.316) with its GivEnergy 8.2 kWh battery (BMS firmware 3020) using the Raspberry Pi capture box in [capture-box/](../capture-box/README.md), from 17:05 UTC on 26 September to 15:57 UTC on 27 September 2026, about 23 hours. The dongle was tapped on the battery's "Batt to Batt" comms terminals, and GivTCP's MQTT output was recorded alongside. The capture covers an evening of discharge, a forced overnight charge to 100%, the night at full charge, and the next day, when SoC stayed between 83% and 100%. The inverter setting HR109 `enable_bms_read` was 1, and HR111/HR112 (charge/discharge limit) were both 44.

### Poll cadence and turnaround

The pattern matches the 90-hour capture above. In 7 hours on 27 September:

| Query | Requests | Cadence | BMS turnaround |
|---|---:|---|---|
| Device 1 FC3 HR0 to HR27 | 103,122 | every 245.8 ms (236 to 482 ms) | 101 ms median (97 to 105) |
| FC4 block 1 (start 0x00, count 21), devices 1 to 5 | about 250 each | about every 10 s in bursts, 100 s on average | 87 ms median |
| FC4 blocks 2 and 3 (0x15/19 and 0x28/20), devices 1 to 5 | about 126 each | every 200.5 s | 83 to 85 ms median |

Every request got a reply, including those for devices 2 to 5. With one battery fitted, the master battery answers for the absent packs with the empty-slot reply described in [03-input-registers.md](03-input-registers.md): all zeros, with the temperature fields at `0xF556` (-273.0 C).

### The limits during a full charge

The battery reported HR25 = 90.00 A and HR26 = HR27 = 80.00 A throughout, except at the very top of the charge.

| Time (UTC) | SoC | Pack voltage (HR22) | Charge current | HR26 | HR27 |
|---|---|---|---|---|---|
| 22:30 to 23:15 | 59% to 90% | 53.4 V to 54.35 V | 60.5 A | 80 A | 80 A |
| 23:20 to 23:35 | 93% to 98% | 54.3 V to 55.2 V | 43.6 A down to 14.6 A | 80 A | 80 A |
| 23:40 | 99% | 56.9 V | 2.9 A | **3.20 A** | 80 A |
| from 23:45 | 100% | about 56.1 V to 56.6 V | about 0 A | 3.20 A | 80 A |

Three things follow:

- **HR26 caps charging on a G3 LV.** When the BMS cut HR26 to 3.20 A and left HR27 at 80 A, the charge current dropped to about 2.9 A at once and stayed under HR26, as the labels in [02-holding-registers.md](02-holding-registers.md) say. The G3 LV DSP firmware agrees (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)).
- **The inverter tapers the charge itself before the BMS does.** The current held at about 60.5 A by HR23 up to 90% SoC. The inverter's own reading was about 65 A and a steady 3.47 kW to 3.49 kW, which is its 3.6 kW battery power limit (GivTCP's `Invertor_Max_Bat_Rate` is 3600), not a current cap. Then it fell to about 14.6 A over 20 minutes with HR26 and HR27 still at 80 A. An emulator doesn't need to produce this taper; the inverter does it.
- **At full charge the BMS keeps HR26 at 3.20 A**, and the inverter tops the pack up every so often at about 3 A for a few minutes, with short discharges of about 2.9 A in between. The BMS held HR26 at 3.20 A from before midnight until 04:37 UTC. Then it released it in steps of 10 A every 11 s (13.20 A, 23.20 A and so on up to 73.20 A), and then to 80.00 A.

### Voltages at the top of the charge

The inverter's own battery voltage reading (GivTCP) was 0.2 V to 0.3 V above HR22 at rest and about 1.3 V above it at 60 A, the drop in the battery cable. At 100% the pack sat at about 56.1 V to 56.6 V (median 56.13 V by HR22). The 3 A top-ups briefly took HR22 to 57.53 V and the inverter's reading to 57.61 V, with no fault raised.

There were three top-ups after the main charge:

| Top-up (UTC) | Charge current | HR22 | Inverter's reading | HR20 while charging |
|---|---|---|---|---|
| 23:38 to 23:43, end of the main charge | 14.7 A down to 2.8 A | 55.39 V to 57.06 V | up to 57.37 V, above 57.0 V for about 3 minutes | 0 |
| 00:35 to 00:37 | 2.9 A | 56.13 V to 57.13 V | up to 57.35 V, above 57.0 V for about 2.5 minutes | 0 |
| 01:21 to 01:22 | 2.9 A | 56.66 V to 57.52 V | up to 57.61 V, above 57.0 V for about 2 minutes | 0 |

HR20 bit 2 (over-voltage) was clear during every charge. It was set only when the last top-up ended, at the 57.52 V peak of HR22, and it stayed set for 271 s while the pack discharged at about 2.8 A (HR19 bit 5, see [02-holding-registers.md](02-holding-registers.md#evidence-from-my-g3-capture-september-2026)). HR22 fell from its peak and was above 57.0 V for only about 70 s of that time.

The inverter settings HR98 and HR97 were 58.5 V and 43.2 V all night. The G3's over-voltage trip comes from HR98 and is at 59.5 V for 1 s on the inverter's own reading (see [05-inverter-firmware.md](05-inverter-firmware.md#battery-voltage-checks)), so readings up to 57.61 V for minutes with no fault are what the firmware predicts. The inverter's status stayed normal all night.

### Discharge

During the evening the battery discharged from 99% down to 58% SoC. Over the whole capture the largest discharge current was 70.45 A (about 3.6 kW), and the lowest pack voltage was 52.11 V. The largest charge current was 61.07 A. HR27 stayed at 80.00 A throughout.

### No writes to the battery

The capture holds 478,645 frames with no framing errors. The inverter sent only FC=3 and FC=4: 233,594 HR polls and 5,729 IR polls. There was no FC=6 write at all in 23 hours. So in normal running a G3 LV doesn't write to the battery. The DSP has FC=6 write paths on counters (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)), but their conditions didn't occur here.

### Status bits

Once the battery had started up, HR19 took only four values: `0xCF`, `0xCE`, `0xC7` and `0xEF`. `0xC7` is bit 3 clear at high cell voltage, and `0xEF` is bit 5 set at full charge (see [02-holding-registers.md](02-holding-registers.md#evidence-from-my-g3-capture-september-2026)). HR20 was either 0 or `0x0004` (bit 2, over-voltage), as after the last top-up described above.

### Gaps in this capture

The poll gaps in this capture were the capture box being off or restarting, not the inverter. The first box's Wi-Fi also dropped several times (see the capture-box README), which didn't affect the wire log. From 27 September the logger writes a start marker, so `tools/capture_checks.py` can tell capture gaps from inverter restarts.

### Discharge to the reserve and a full charge (27-29 September)

I ran a further capture on my G3 LV from 27 to 29 September 2026, over two million frames with no framing errors, covering a slow discharge to the reserve, a forced discharge to the reserve, and a full charge back up from there. The battery is the same GivEnergy 8.2 kWh Gen 1 pack (BMS firmware 3020).

GivEnergy's own retired-product figures for this pack give a true capacity of 10.24 kWh / 200 Ah, a usable capacity of 8.192 kWh / 160 Ah ("100% DoD"), 51.2 V nominal, and a maximum of 85 A / 4.096 kW. HR11 reports 160 Ah on my battery. Block 2's calibrated capacity had fallen to 147.66 Ah after 740 cycles.

**Discharge to the reserve.** Two runs reached the floor:

- 27 September, a slow evening discharge under about 7 A: HR21 reached 5% at 22:30 UTC. Pack voltage 50.73 V, lowest cell 3.167 V, cell spread about 10 mV.
- 28 September, a forced discharge at about 71 A: HR21 reached 4% at 21:29:01 UTC, and the inverter stopped within about a second of that reading (current stepping -71 A, -22.6 A, -0.2 A). Under 71 A at 5%, just before the stop, the pack had sagged to 49.41 V and the lowest cell to 3.087 V. At rest afterwards the pack recovered to 50.9-51.0 V and the cells to 3.18-3.19 V, still on the LFP plateau. That's a hidden buffer below the inverter's "0%", consistent with 160 Ah usable out of a true 200 Ah.

At the floor the BMS didn't soften anything: HR27 (discharge limit) stayed at 80.00 A all the way to 4%, HR20 stayed 0, and HR19 bit 3 never cleared, unlike the 90-hour G3 capture above, where bit 3 flickered at the floor. On mine, HR19 just toggled between `0xCF` and `0xCE` with the sign of the near-zero current (bit 0). The battery held at 4% for 61 minutes, within 0.1 A throughout, and SoC never went below 4%, so the DSP's floor force-charge (see [05-inverter-firmware.md](05-inverter-firmware.md#what-the-dsp-does-with-the-bms-status-registers)) never triggered.

**A full charge from the reserve.** The off-peak charge started at 28 September 22:30:12 UTC (23:30 BST) from 4% SoC and 51.09 V, straight to about 61 A with no ramp:

| SoC | Charge current | Notes |
|---|---|---|
| 4% to 91% | steady ~60.5 A by HR23 (about 65 A and 3.49 kW by the inverter's reading) | about 2 h 5 min; roughly 1% per 88 s, which is 1.6 Ah per % of 160 Ah |
| 91% to 97% | 59.8, 54.4, 48.9, 43.1, 37.6, 31.7, 25.9 A | the inverter's own taper, about -5.8 A per %, with HR26 still at 80 A |
| 98% (00:52 UTC) | cut to 8.00 A | cells jumped from about 3.43 V to 3.52-3.59 V, pack 56.62 V; BMS cuts HR26 |
| 99% | cut to 3.20 A | pack 57.40 V, highest cell 3.590 V, top spread 68 mV (against about 10 mV at the bottom) |
| 100% (00:58:18 UTC) | about -3.2 A | BMS sets HR20 = `0x0004` and HR19 = `0xEF` (bit 5); the DSP forces a small discharge |

That knee, and the HR19 bit 5 / HR20 bit 2 behaviour at full charge, match the September top-up findings above and the firmware analysis in [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus). HR26 later released back to 80 A in +10 A steps, as in the earlier capture.

No FC=6 write appeared anywhere in this capture either.

See [07-emulator-implications.md](07-emulator-implications.md) for what the reserve behaviour and the hidden buffer mean for an emulator.

### A solar charge to full and the trickle release (29 September)

The same capture ran on into 29 September, 07:23 to 20:05 UTC, 602,677 frames with no framing errors and no FC=6 write. This stretch covers a charge from solar rather than a forced charge, and what happens after the BMS cuts the current at the top.

Solar current varies with the sun, up to 51.6 A on my system that day. At 98% SoC the BMS cut HR26 the same way it did during the forced charge on 28-29 September: 80.00 A, then 32.00 A, then 8.00 A, then 3.20 A, all within 2.5 minutes (12:24:26, 12:25:20 and 12:27:01 UTC), as the highest cell passed about 3.47-3.50 V. So the cut follows the highest cell, not how the charge is driven.

At 100% HR20 bit 2 and HR19 bit 5 pulsed 976 and 975 times that day, about 16 minutes in total, each pulse matching the DSP's small forced discharge of about -3.2 A, as in the earlier capture. At rest at 100% the highest cell sat at 3.46-3.49 V for about 2.5 hours (lowest cell 3.43-3.45 V), and the spread at the knee reached 79 mV, against 68 mV the night before.

**The trickle release.** The BMS held HR26 at 3.20 A for 2 hours 40 minutes, from 12:27 to 15:07 UTC, then released it at 15:07:43 in steps of +10 A about every 11 seconds back up to 80.00 A. The release came just after the pack started discharging and the highest cell fell through about 3.40 V:

| Time (UTC) | Highest cell | Pack current | HR26 |
|---|---|---|---|
| 14:53 | 3.480 V | at rest | 3.20 A |
| 15:03 | 3.432 V | -5 to -8 A | 3.20 A |
| 15:06 | 3.404 V | -5 to -8 A | 3.20 A |
| 15:07:43 | below 3.40 V | -5 to -8 A | releases: 13.20, 23.20, ... 80.00 A |

On the early-morning full charge from the reserve (29 September), the same hold lasted longer: 3 hours 43 minutes at 3.20 A before release. Different hold times, same trigger: the release follows the pack coming off the top and the highest cell falling to about 3.40 V, not a fixed timer.

HR15 bit 0 was set again during this solar charge, for 26,297 polls, and clear the rest of the day, matching the earlier correction that bit 0 marks charging, not 100% SoC (see [02-holding-registers.md](02-holding-registers.md#register-15-bits)).

Other ranges that day: SoC 46% to 100%, discharge current up to 71.2 A, pack voltage (HR22) 51.57 V to 57.50 V, battery temperature (HR24) 23 to 27 degC.

## Capture experiments worth running

To resolve remaining open questions, useful targeted captures would be:

| Capture scenario | Resolves |
|---|---|
| Charge from grid (Eco mode) | Reg 11 transition triggers; charge-mode bit positions |
| Battery calibration (HR29 non-zero) | HR20 bits 2 and 3 as the calibration end points; any FC=06 writes |
| Inverter cold boot | First-byte-after-power-on probe sequence (if any) |
| Imbalance condition | Balancing-active flag identification |
| Multi-battery added/removed | "Device appears" / "device disappears" handling |

The G3 captures above have already covered discharge under load (HR23 is the pack current in 0.01 A, HR21 the SoC), the low-SoC floor (HR19 bit 3, HR27 cuts on a Gen 3 battery) and forced charge and discharge (no FC=06 writes). An earlier row here expected force-charge writes to address `0x00E7`. That address belongs to the inverter's device `0x11` meter path, not to the battery (see [05-inverter-firmware.md](05-inverter-firmware.md#a316--hy-series-armstorebin)).

## Validation campaign methodology

The analysis in this repository was extended by running a controlled validation campaign against a real GivEnergy LV system, using three time-aligned data streams:

1. **RS485 wire sniff** via `tools/serial_hexdump_logger.c` (a USB-RS485 dongle in parallel passive-tap mode at the inverter BMS terminal block).
2. **Modbus TCP poll** of the inverter's local API via `tools/tcp_poller.py` at 1 Hz, providing the inverter's own published interpretation of BMS state -- used as ground-truth labels for wire-side decoding.
3. **Scenario annotations** via `tools/tag.py`, manual at the boundary of forced transitions (force-charge, force-discharge, current-limit step) and auto-derived from the TCP stream's mode-change events.

The three streams are post-hoc time-aligned via `tools/join_streams.py` into a single parquet keyed by NTP wall-clock timestamp. `tools/analysis_template.ipynb` provides a starting point for the analysis itself, structured as PACE-hypothesis-first per-unknown sections (see [09-pace-comparison.md](09-pace-comparison.md) for why PACE is the natural hypothesis source).

To reproduce on your own system:

1. Configure `~/.givenergy-redact.toml` with your serials and IPs (used by `tools/redact.py` before sharing any artefact).
2. Run a 48-72 hour passive capture under your normal solar/load cycle.
3. Optionally run a 30-45 minute active session forcing high-SoC dwell, low-SoC dwell, and current-limit changes.
4. Run `tools/join_streams.py` to produce the parquet.
5. Open `tools/analysis_template.ipynb`, point it at your capture directory, and work through each unknown section.
6. Run `python tools/capture_checks.py joined.parquet` for the three G3 LV checks: which of HR26/HR27 the current follows, the end-of-charge taper, and cold-boot acceptance time. Capture a full charge, a discharge to the SoC floor and at least one inverter power cycle to give it something to find.
