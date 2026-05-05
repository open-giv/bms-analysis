# BMS firmware static analysis

The official GivEnergy BMS firmware can be statically analysed to reveal the protocol implementation in detail. This complements the empirical wire captures by showing exactly how the BMS responds to queries, what data structures back the registers, and which validation rules apply.

## Firmware format

GivEnergy distributes the BMS firmware as `BMS_ARM.bin` files (one per firmware version). The format:

- 4-byte vendor header: `0xVVVV 0x5566` (version little-endian + magic)
- Standard Cortex-M vector table at file offset 4 (mapping to flash `0x08000000`)
- Plain Thumb-2 code, no compression or encryption

Versions analysed:

| Version | Size | Notes |
|---:|---:|---|
| 3017 | 143,370 bytes | Older code structure; major refactor before 3020 |
| 3020 | 118,794 bytes | Functionally identical to 3022 for the wire protocol |
| 3022 | 119,818 bytes | Reference version for analysis below |
| Gen 3 / 4xxx | ~165 KB | Different protocol architecture, not analysed here |

3020 and 3022 produce byte-identical wire behaviour - addresses shift but field meanings don't change. 3017 has the same fields but different code structure.

## MCU identification

**STM32F1xx connectivity-line variant** (likely STM32F103xC/D/E or F105/F107). Determined from peripheral-base address references in literal pools:

- GPIOA-D
- USART1, USART2, USART3 (peripheral bases at `0x4001_3800`, `0x4000_4400`, `0x4000_4800`)
- UART4 (`0x4000_4C00`), UART5
- ADC1/2, SPI1
- DMA1

The presence of UART4 / UART5 rules out the basic STM32F103C8T6 (which the inverter side uses).

Initial SP `0x2000_9BD0` (~40 KB SRAM stack pointer) - consistent with STM32F103xC (256 KB flash / 48 KB SRAM) or higher density.

## Modbus dispatcher

Located at flash address **`0x0800_E1B8`**. The function reads the FC byte from the RX buffer at SRAM `0x2000_385C + 1`, then runs through a `cmp r0, #3 / cmp r0, #4 / cmp r0, #6` chain. Anything else falls into a default branch that returns Modbus exception 0x80|FC with code 1 ("Illegal Function").

Maximum register count per FC=3 / FC=4 read is `0x80` (128). Exceeding this returns exception code 2 (FC=3) or 4 (FC=4).

| Function | Flash address | Notes |
|---|---|---|
| FC dispatcher entry | `0x0800_E1B8` | The cmp #3/#4/#6 chain |
| FC=3 handler | `0x0800_DD82` | The for-loop that emits standard `device + FC + byte_count + data + CRC` |
| FC=4 handler | `0x0800_DEBC` | Emits the non-standard `device + FC + addr_echo + data + CRC` |

## RX/TX frame buffer

SRAM `0x2000_385C`. Layout: `[0]=device_addr, [1]=FC, [2]=addr_hi, [3]=addr_lo, [4]=count_hi or value_hi, [5]=count_lo or value_lo`.

## FC=3 backing store - the holding-register table

The firmware's FC=3 holding registers are a **flat array of 200 (`0xC8`) 16-bit halfwords at SRAM `0x2000_39C0`**. Register N is just `*(uint16_t*)(0x2000_39C0 + N*2)`.

The FC=3 handler is essentially:

```
for i in range(start, start + count):
    tx.append(htons(table[i]))
```

No per-register handler logic. The table is just a SRAM mirror that other tasks populate. To find which firmware code "owns" a particular register, search for stores to its specific offset.

| Function | Flash address | What it does |
|---|---|---|
| Init function | `0x0800_D534` | Clears all 200 regs to `0xFFFF`, then writes specific defaults (firmware version, hardware-rev constant, serial number bytes, etc.) |
| Update function | `0x0800_D584` | Recomputes volatile fields each cycle (e.g. the various status / counter fields) |

## FC=4 field encoding (mixed: some raw, some `-2730`-offset)

The FC=4 handler at `0x0800_DEBC` populates the response from a per-pack structure (145 bytes per pack at SRAM `0x2000_3D6A`, indexed by `device_address - 1`, supports up to 6 packs).

**Field-by-field encoding is mixed**. Some fields are emitted with `(stored_value - 2730)` via `subw r1, r1, #0xAAA`; others are emitted directly. The seven `subw` sites and what they encode:

| `subw` flash addr | IR Block | Wire byte offset | Field | Encoding |
|---|---|---|---|---|
| `0x0800_DF8C` | Block 1 | 22-23 | Temperature 1 | `(decidegC + 2730)` -> `subw` -> wire = raw decidegC |
| `0x0800_DF98` | Block 1 | 24-25 | Temperature 2 | same |
| `0x0800_DFA4` | Block 1 | 26-27 | Temperature 3 | same |
| `0x0800_DFB0` | Block 1 | 28-29 | Temperature 4 | same |
| `0x0800_DFBC` | Block 1 | 30-31 | Temperature 5 | same |
| `0x0800_E0C0` | Block 3 | 32-33 | unknown | wire = `value - 2730`; decoder adds 2730 |
| `0x0800_E0CE` | Block 3 | 34-35 | unknown | wire = `value - 2730`; decoder adds 2730 |

**Per-cell voltages** at Block 3 bytes 0-31 do NOT pass through `subw` - the cell loop at `0x0800_E0A0..0x0800_E0BE` writes them as raw mV. Confirmed by wire data (`0x0CF4` = 3316 mV directly).

**Max / min cell voltage** at Block 3 bytes 36-39 are also raw mV (no `subw`).

**Block 2 fields** (cycles, capacities, pack voltage, SoC, firmware version) all use direct strb without `subw`.

The internal storage bias of `+2730` for temperatures is presumably to keep them as unsigned uint16 (so -30.0 deg C internal = 2400, well above zero). The same bias appears in the inter-pack PACE protocol on UART4. The two unknown Block 3 fields probably similarly use the bias for some signed mV-like quantity.

**HR reg 24 also encodes `(min_cell_mV - 2730) / 10`** via a separate `subw` at `0x0800_D76A` (writes to the HR table backing store, not to the FC=4 response).

For an emulator: emit cells / max / min as raw mV; emit Block 1 temps as raw decidegC (signed int16 if you need negative temperatures); emit Block 3 bytes 32-35 as `(your_mV_value - 2730)`.

## Inter-pack PACE channel (UART4)

The BMS firmware also implements a **PACE / Pylontech-compatible protocol on UART4** (peripheral base `0x4000_4C00`), used for communication between paralleled batteries in a stack.

- Protocol version `0x25`, CID1 `0x46` (LiFePO4 device class)
- Standard PACE wire format: `~ VER ADR CID1 CID2 LENGTH INFO CHKSUM \r`
- The `0xAAA` offset on cell voltages mentioned above is an artefact of this internal protocol

This is independent of the inverter-side Modbus link and is not relevant for an inverter <-> BMS emulator.

## Bit-banged I2C EEPROM

The firmware bit-bangs an I2C bus on PB6/PB7 to a 24Cxx-family EEPROM at I2C address `0x50`. The EEPROM stores calibration data, serial number, lifetime energy counters, and similar persistent state.

This is internal to the BMS and not exposed on the inverter Modbus link.

## Reproducing the analysis

The firmware binaries are distributed by GivEnergy as part of their "BMS update" tooling. Once obtained, basic analysis can be done with [Capstone](https://www.capstone-engine.org/) (Python bindings), Ghidra, or any STM32-aware disassembler.

Useful entry points to start from:

| Address | What's there |
|---|---|
| File offset 4 onwards | Cortex-M vector table (reset vector at idx 1) |
| Flash `0x0800_E1B8` | Modbus FC dispatcher (the cmp #3/#4/#6 chain) |
| Flash `0x0800_DD82` | FC=3 handler |
| Flash `0x0800_DEBC` | FC=4 handler (non-standard response framing) |
| Flash `0x0800_D534` | FC=3 table init function |
| SRAM `0x2000_39C0` | Holding-register table backing store |
| SRAM `0x2000_385C` | Modbus RX/TX frame buffer |

To map a specific HR register to its source field, search the firmware for stores to `0x2000_39C0 + 2*N`. The function performing the store is the field's owner.
