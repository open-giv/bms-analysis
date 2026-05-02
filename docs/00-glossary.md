# Glossary

A reference for terms used throughout this documentation. Aimed at readers who aren't already deep in embedded-systems / industrial-protocol jargon.

## Protocol terms

**Modbus / Modbus-RTU**: A simple master-slave protocol widely used in industrial and embedded systems. "RTU" = Remote Terminal Unit, the binary variant (as opposed to ASCII). One device (the master) sends requests; one or more devices (slaves) respond. Each frame includes a slave address, a function code, payload, and a CRC.

**RS485**: A robust, differential, multidrop electrical bus standard for serial data. Allows multiple devices on the same pair of wires, with longer cable runs than RS232 (up to ~1.2 km). The GivEnergy BMS link uses RS485.

**Multidrop**: A bus topology where multiple devices share one cable. Only one device transmits at a time; others listen. RS485 supports up to 32 standard-load devices on a bus.

**Half-duplex**: Devices can transmit OR receive but not both at the same time. RS485 is half-duplex.

**DE/RE**: "Driver Enable" / "Receiver Enable" - control pins on RS485 transceiver chips that switch between transmit and receive modes. The BMS firmware toggles a GPIO pin to drive these around its own transmissions. Most USB-RS485 dongles handle DE/RE automatically.

**8N1**: Serial framing - 8 data bits, No parity, 1 stop bit per byte. The standard configuration for Modbus-RTU.

**Baud rate**: Bits per second on the wire. The GivEnergy BMS bus runs at 9600 baud (= 9600 bits/sec ~ 960 bytes/sec accounting for start/stop bits).

**Frame**: A complete Modbus message - one request or one response. Frames are delimited by inter-frame silence (>=3.5 character times of no transmission, ~3.6 ms at 9600 baud).

## Modbus function codes

Function codes (FC) tell the slave what operation to perform. The byte after the slave address.

| FC | Hex | Name | Description |
|---:|---|---|---|
| 3 | `0x03` | Read Holding Registers | Read N consecutive 16-bit registers from the "holding" bank (read/write space). |
| 4 | `0x04` | Read Input Registers | Read N consecutive 16-bit registers from the "input" bank (read-only space). |
| 6 | `0x06` | Write Single Holding Register | Write one 16-bit value to one holding register. |
| 16 | `0x10` | Write Multiple Holding Registers | Write N consecutive registers. (Not implemented by GivEnergy BMS.) |
| 23 | `0x17` | Read/Write Multiple Registers | Combined operation. (Not implemented.) |

The GivEnergy BMS only implements **FC=3, FC=4, and FC=6**. Other FCs return a Modbus exception.

**HR / Holding Register**: A 16-bit register in the slave's "read/write" register space (FC=3 reads them, FC=6 writes them).

**IR / Input Register**: A 16-bit register in the slave's "read-only" register space (FC=4 reads them; there's no FC to write them).

**Modbus exception**: An error response from the slave. The FC byte has its high bit set (`FC | 0x80`); the next byte is the exception code (1 = Illegal Function, 2 = Illegal Address, etc.).

## CRC

**CRC / CRC-16**: Cyclic Redundancy Check, a checksum that detects bit errors in transmission. Modbus uses CRC-16 with polynomial `0xA001` and initial value `0xFFFF`. Appended to every frame as 2 bytes (low byte first, high byte second on the wire).

**`auchCRCHi` / `auchCRCLo`**: The two 256-byte lookup tables used by the canonical Modbus CRC implementation. Their byte patterns are distinctive (`auchCRCHi` starts `00 c1 81 40 01 c0 80 41 ...`) and can be used to identify Modbus implementations in firmware binaries.

## Battery / electrical terms

**BMS**: Battery Management System. The microcontroller-based circuit that monitors cell voltages, currents, temperatures, and protects the battery from over/under-charge, over-current, etc.

**LFP / LiFePO4**: Lithium Iron Phosphate - a lithium-ion battery chemistry favoured for stationary storage. Lower energy density than NMC but safer, longer cycle life, and cobalt-free. Cell voltage range typically ~2.5 V (empty) to ~3.65 V (full), with nominal ~3.2 V.

**Cell**: A single electrochemical unit. A 16-cell LFP "16S" pack has 16 cells in series, giving ~51.2 V nominal pack voltage.

**Pack / Battery / Module**: Different vocabulary for the same idea - a complete battery unit consisting of multiple cells plus a BMS. GivEnergy's "9.5 kWh battery" is one pack with 16 LFP cells in series.

**Slice**: Internal terminology in some BMSes for a sub-board that monitors a subset of cells. Some firmware analysis references suggest the BMS supports up to 6 slices.

**SoC**: State of Charge. How full the battery is, as a percentage.

**DoD**: Depth of Discharge. How much has been used, as a percentage. `DoD = 100 - SoC`.

**Pack voltage**: Total voltage across the series-connected cells. For a 16S LFP at nominal 3.2 V: 51.2 V. Range over usable charge: ~48 V (deep discharge) to ~58 V (fully charged).

**Cell balancing**: Equalising state-of-charge across cells. Most BMSes do "passive balancing" by bleeding charge from the highest-voltage cells through resistors during charging.

**AFE**: Analog Front-End. The IC (or sub-circuit) that does precision measurement of cell voltages and currents. Often a dedicated chip like a TI BQ76xxx, LTC68xx, or similar; or a daughterboard with its own MCU.

## Inverter terms

**Inverter**: Converts DC (battery / solar panels) to AC (mains-grid). For a battery-storage system, the inverter manages charging from grid/solar and discharging to grid/loads.

**Hybrid inverter**: Combines a solar inverter and a battery inverter in one unit. GivEnergy's HY-series and FA-series are hybrids.

**AC-coupled**: Architecture where the battery has its own inverter, connected to the AC side of an existing solar system. GivEnergy's "AC 3.0" is AC-coupled.

**AIO / All-In-One**: A single product combining inverter + battery + MPPT + sometimes more. GivEnergy's "All-In-One" line.

**MPPT**: Maximum Power Point Tracker - the circuit that extracts maximum power from a solar panel string by adjusting voltage to match panel characteristics.

**Gen 1 / 2 / 3**: GivEnergy product generations. Different hardware revisions, sometimes different firmware. Same Gen 2 LV battery is compatible with G1, G2, G3 hybrid and AC 3.0 inverters.

**HV / LV**: High-Voltage / Low-Voltage battery families. LV typically ~48-58 V (single 16-cell LFP pack). HV stacks multiple modules in series for hundreds of volts. **This documentation covers LV only.**

## Embedded / firmware terms

**MCU**: Microcontroller Unit. A single chip combining a CPU, memory, and peripherals.

**Cortex-M**: ARM's family of microcontroller cores. The GivEnergy BMS uses a Cortex-M MCU (specifically STM32F1xx).

**Thumb / Thumb-2**: The 16-bit instruction set used by Cortex-M (Thumb-2 adds some 32-bit instructions). All firmware analysis here is in Thumb mode.

**STM32**: ST Microelectronics' family of Cortex-M MCUs. The GivEnergy BMS firmware targets STM32F103-class parts.

**DSP**: Digital Signal Processor. A specialised CPU for high-rate signal processing (like power-electronics control loops). The GivEnergy inverters use a TI C2000 DSP for the power-stage control loop, separate from the ARM Cortex-M MCU that handles communications.

**USART / UART**: A hardware peripheral that does serial communication. The GivEnergy BMS uses USART3 for the Modbus link to the inverter (over an RS485 transceiver).

**SPI / I2C**: Other serial peripheral types, used for chip-to-chip communication on a board. Not exposed externally.

**ADC**: Analog-to-Digital Converter - reads voltage levels from an analog input pin. Used for cell voltage measurement.

**GPIO**: General-Purpose Input/Output pin. Used for things like RS485 DE/RE control or status LEDs.

**Flash**: Non-volatile memory that holds the firmware. STM32F1xx flash starts at address `0x08000000`.

**SRAM**: Volatile memory used for runtime state. STM32F1xx SRAM starts at `0x20000000`.

**Vector table**: A list of function pointers at a fixed location (start of flash) - the first entry is the initial stack pointer, the second is the reset handler, then exception/interrupt handlers.

## Reverse-engineering tools

**Static analysis**: Examining a binary by reading its instructions without actually running it. Distinguishes from dynamic analysis (running the code with a debugger).

**Disassembly**: Converting machine-code bytes back into human-readable assembly instructions.

**Capstone**: An open-source disassembly engine, easy to use from Python. Used for the firmware analysis here.

**Ghidra**: A more featured reverse-engineering suite (by NSA) - decompiles to C-like pseudo-code, supports many architectures.

**Wire capture / trace**: A recording of the bytes flowing on a communications bus. Captured by a hardware sniffer (e.g. USB-RS485 dongle in monitor mode).

## Project-specific

**FC=4 non-standard format**: GivEnergy's BMS uses a custom FC=4 response format that echoes the request's start address in place of the standard byte_count field. Critical for emulator implementations - see [01-protocol.md](01-protocol.md).

**HR poll**: The high-rate (~245 ms) FC=3 query the inverter sends to the primary battery for real-time status.

**IR poll**: The lower-rate FC=4 queries the inverter sends to all batteries for telemetry. Three blocks per battery: Block 1 (regs 0..0x14), Block 2 (regs 0x15..0x27), Block 3 (regs 0x28..0x3C).

**"Absent slave" pattern**: The distinctive empty response pattern (mostly zeros plus a recurring `f5 56 f5 56...` sequence) returned for slave addresses where no battery is physically present but the inverter still polls. See [03-input-registers.md](03-input-registers.md).
