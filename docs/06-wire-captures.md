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
2026-05-01 07:23:39.416  00000000  01 03 00 00 00 1C 44 03                          |......D.|
2026-05-01 07:23:39.516  00000008  01 03 38 00 65 FF FF FF FF FF FF FF FF XX XX XX  |..8.e...........|
2026-05-01 07:23:39.516  00000018  XX XX XX XX XX XX XX FF FF 00 BA 00 30 0B CE 00  |..............0.|
...
```

(Serial bytes redacted with `XX` placeholders. In a real capture, bytes 13-22 of the HR response carry the BMS serial as ASCII.)

Each line shows: timestamp, byte offset into the capture stream, up to 16 hex bytes, and the ASCII rendering of those bytes.

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

## Capture experiments worth running

To resolve remaining open questions, useful targeted captures would be:

| Capture scenario | Resolves |
|---|---|
| Discharge under significant load | Reg 23 (current) magnitude / sign behaviour; reg 21 (suspected SoC) decreasing |
| Charge from grid (Eco mode) | Reg 11 transition triggers; charge-mode bit positions |
| Force-charge or force-discharge | FC=06 write traces to address 0x00E7 (control byte) |
| Low-SoC condition (~10%) | Warning/fault bits in reg 19 |
| Inverter cold boot | First-byte-after-power-on probe sequence (if any) |
| Imbalance condition | Balancing-active flag identification |
| Multi-battery added/removed | "Device appears" / "device disappears" handling |

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
