# GivEnergy BMS Analysis

Documentation and analysis of the GivEnergy Gen 2 LV battery BMS protocol. With GivEnergy in administration as of 2026, the goal is to keep installed kit useful by opening up the protocol enough for two complementary integrations:

1. **Third-party LFP battery + GivEnergy inverter** - an emulator pretends to be a GivEnergy BMS so a cheaper LFP pack can be used in place of an out-of-warranty / unobtainable original. See [docs/07-emulator-implications.md](docs/07-emulator-implications.md).

2. **GivEnergy battery + third-party inverter** - a bridge reads the GivEnergy battery and re-presents it on a standard protocol (Pylontech CAN being the prime target, supported by Victron, Deye, Goodwe, Sungrow, Sofar and many others). See [docs/08-bridge-implementation.md](docs/08-bridge-implementation.md).

Plus the obvious side-benefit: BMS health monitoring and diagnostics directly from the battery, without going through GivEnergy's cloud.

## Background

The original empirical analysis - hardware setup, RS485 captures, raw hex traces, and field-by-field interpretations - was started by @kenbell in [NOTES.md](NOTES.md). This documentation expands on that empirical work with:

- Static analysis of the official BMS firmware (multiple versions: 3017, 3020, 3022)
- Static analysis of multiple inverter firmware variants (FA-series, A316/HY, A920/AIO, etc.)
- Wire-capture parsing and timing analysis
- Implementation guidance for both emulator (Goal 1) and bridge (Goal 2) directions

## Documentation index

| File | Topic |
|---|---|
| [docs/00-glossary.md](docs/00-glossary.md) | Glossary of terms (Modbus, FCs, embedded, battery, etc.) - **start here if jargon trips you up** |
| [docs/01-protocol.md](docs/01-protocol.md) | Modbus framing, baud rate, CRC, function-code support, FC=4 non-standard format |
| [docs/02-holding-registers.md](docs/02-holding-registers.md) | HR(0..27) layout, field-by-field interpretation, polling cadence |
| [docs/03-input-registers.md](docs/03-input-registers.md) | IR Block 1/2/3, layouts, cell voltages, slave rotation, "absent slave" pattern |
| [docs/04-bms-firmware.md](docs/04-bms-firmware.md) | BMS firmware static analysis - MCU, register table, FC handlers, internal architecture |
| [docs/05-inverter-firmware.md](docs/05-inverter-firmware.md) | Inverter firmware analysis - variants, validation rules, "BMS protocol is the constant" insight |
| [docs/06-wire-captures.md](docs/06-wire-captures.md) | Cadence, latency, IR rotation pattern, capture methodology |
| [docs/07-emulator-implications.md](docs/07-emulator-implications.md) | **Goal 1** - Emulator implementation (3rd-party battery -> GivEnergy inverter) |
| [docs/08-bridge-implementation.md](docs/08-bridge-implementation.md) | **Goal 2** - Bridge implementation (GivEnergy battery -> 3rd-party inverter) |

## Tools

| File | Purpose |
|---|---|
| [tools/serial_hexdump_logger.c](tools/serial_hexdump_logger.c) | Logs all RS485 traffic with timestamps to a file. Useful for protocol analysis. |
| [tools/modbus_register_logger.c](tools/modbus_register_logger.c) | Passively watches the bus for reads/responses involving a specific register, logs that register's value over time (text or CSV). Useful for tracking how a single field varies under known conditions. |
| [tools/parse_log.py](tools/parse_log.py) | Parses serial_hexdump_logger output into Modbus frames with cadence/latency analysis. Handles GivEnergy's non-standard FC=4 framing. |

## System scope

The analysis is based on a GivEnergy "classic" Low-Voltage system using Gen 2 9.5 kWh LiFePO4 batteries. The same batteries are compatible with:

- AC 3.0 inverters
- Gen 1 Hybrid inverters
- Gen 2 Hybrid inverters
- Gen 3 Hybrid inverters (FA-series)

Because the same BMS works with all of these, **the wire protocol is invariant across inverter variants** - inverters' internal firmware differs but they all produce the same Modbus requests on the wire. See [docs/05-inverter-firmware.md](docs/05-inverter-firmware.md) for details.

This analysis does **not** cover High-Voltage (HV) batteries or All-In-One (AIO) inverters; those use different battery families.

## Status

| Topic | State |
|---|---|
| Wire protocol fundamentals | Well-understood |
| Function codes used | Confirmed: FC=3 read, FC=4 read, FC=6 write (rare) |
| HR(0..27) field meanings | ~70% mapped (see [docs/02](docs/02-holding-registers.md)) |
| IR field meanings | Most fields mapped (see [docs/03](docs/03-input-registers.md)) |
| FC=4 framing format | **Non-standard** - resolved (see [docs/01](docs/01-protocol.md)) |
| Inverter validation rules | Lenient on FA, stricter on older variants - mapped |
| BMS alerts / mode flags | Partially understood; needs labelled captures |
| BMS firmware versions covered | 3017, 3020, 3022 (LV); not yet HV or Gen 3 |

## Contributing

Contributions welcome. Common useful contributions:

- More wire captures, especially under specific conditions (charge / discharge / fault / balancing)
- Captures from different inverter variants
- Additional firmware versions
- Emulator (Goal 1) implementations and test reports against real GivEnergy inverters
- Bridge (Goal 2) implementations - especially Pylontech-CAN bridges tested against Victron / Deye / etc.
- Corrections to field interpretations
- Cell-monitor protocol details (currently incompletely documented)
