# GivEnergy BMS Reverse Engineering Notes

These notes are a work-in-progress to document the BMS protocol used by GivEnergy batteries.  Now that GivEnergy support is no longer available, I hope this work can help with using GivEnergy batteries with 3rd party inverters and/or a way to monitor battery health.

This analysis is based on a GivEnergy AC 3.0 inverter with a pair of GivEnergy Gen 2 9.5kWh batteries that are 'known good', by capturing the protocol exchanges between the inverter and the batteries.

This system is the 'classic' GivEnergy Low-Voltage system - it is unknown how much (if any) of this analysis applies to High-Voltage systems or AIOs.

## Top-Level Summary
So far, I think I know this:
1. The BMS protocol is vanilla modbus (RS485) at 9600 baud.
2. The BMS protocol does not appear to be required to use the battery.  All the protocol traces seen to date does not show any 'write' activity by the inverter to actively control the BMS.  It should be possible to use the batteries as generic 51.2V LiFePo batteries with inverters that support.
3. I have a resonable handle on accessing Cell Voltage, Temperatures, Capacity / State Of Charge
4. I have no information about BMS alerts, BMS mode (float, bulk, etc), BMS requests (force-charge, force-discharge, etc)

## RS485 protocol

From scope analysis:
1. Appears to be 9600 baud rate
2. Appears to be initiated by Inverter (no protocol traffic until inverter boots)
3. Appears to be constant communication once inverter boots

## Protocol Traces / Analysis

The protocol appears to be standard modbus.  The modbus ID of each battery (set via dip-switches) appears to directly be its modbus ID.

## Holding Registers
It appears the inverter only queries holding registers from the primary battery (id 1).  This is the most common query (by far) at once every 230ms approximately, so most likely it is providing the critical real-time stats from the BMS.


Traces
```
2026-05-01 07:23:39.416  00000000  01 03 00 00 00 1C 44 03                          |......D.|
2026-05-01 07:23:39.516  00000008  01 03 38 00 65 FF FF FF FF FF FF FF FF 44 58 32  |..8.e........DX2|
2026-05-01 07:23:39.516  00000018  33 31 39 47 30 30 30 FF FF 00 BA 00 30 0B CE 00  |319G000.....0...|
2026-05-01 07:23:39.516  00000028  00 00 00 00 00 11 4B 38 9D 00 CF 00 00 00 5F 14  |......K8......_.|
2026-05-01 07:23:39.516  00000038  BF FF F2 00 11 23 28 20 B2 20 B2 12 6E           |.....#( . ..n|
- - -
2026-05-01 07:27:02.675  0000e345  01 03 00 00 00 1C 44 03                          |......D.|
2026-05-01 07:27:02.778  0000e34d  01 03 38 00 65 FF FF FF FF FF FF FF FF 44 58 32  |..8.e........DX2|
2026-05-01 07:27:02.778  0000e35d  33 31 39 47 30 30 30 FF FF 01 74 00 30 0B CE 00  |319G000...t.0...|
2026-05-01 07:27:02.778  0000e36d  00 00 00 00 00 12 16 38 9D 00 CE 00 00 00 5D 14  |.......8......].|
2026-05-01 07:27:02.778  0000e37d  C5 00 4E 00 11 23 28 41 64 41 64 37 C4           |..N..#(AdAd7.|
- - -
2026-05-01 07:27:02.915  0000e38a  01 03 00 00 00 1C 44 03                          |......D.|
2026-05-01 07:27:03.018  0000e392  01 03 38 00 65 FF FF FF FF FF FF FF FF 44 58 32  |..8.e........DX2|
2026-05-01 07:27:03.018  0000e3a2  33 31 39 47 30 30 30 FF FF 01 74 00 30 0B CE 00  |319G000...t.0...|
2026-05-01 07:27:03.018  0000e3b2  00 00 00 00 00 12 16 38 9D 00 CE 00 00 00 5D 14  |.......8......].|
2026-05-01 07:27:03.018  0000e3c2  C4 00 4E 00 11 23 28 41 64 41 64 33 38           |..N..#(AdAd38|
```

| Range | Example Raw Value(s) | Interpretation |
|-|-|-|
|0-1|00 65|Unknown|
|2-9|FF FF FF FF FF FF FF FF|Unknown|
|10-19|44 58 32 33 31 39 47 30 30 30|Serial Number Batt 1 (=DX2319G000)|
|20-21|FF FF|Unknown|
|22-23|00 BA|Unknown (=186) **varies over time**|
|24-25|00 30|Unknown (=48)|
|26-27|0B CE|Firmware Version|
|28-33|00 00 00 00 00 00|Unknown|
|34-35|11 4B|Unknown (=4427) **varies over time**|
|36-37|38 9D|Unknown (=14493)|
|38-39|00 CF / 00 CE|Unknown (=207)|
|40-41|00 00|Unknown|
|42-43|00 5F / 5D|Unknown (=95)|
|44-45|14 BF|Unknown (=5311) **varies over time**|
|46-47|FF F2 / 00 4E|Unknown (=65522#-14 / 78) **Current?**|
|48-49|00 11|Unknown (=17)|
|50-51|23 28|Unknown (=9000)|
|52-53|20 B2/41 46|Unknown (=8370)|
|54-55|20 B2|Unknown (seems to always repeat 52-53)|


## Input Registers

The inverter appears to query input registers from both connected batteries periodically.

The registers appear to be requested in these blocks:

| Block | Possible Purpose |
|-|-|
| 0x00 - 0x15 | Batt Serial + Temps? |
| 0x16 - 0x27 | Batt Status |
| 0x28 - 0x3C | Cell Voltages |

It appears at least some data is not actually aligned into 16-bit register boundaries, so data is documented as offsets from the start of the block.

### Block 1 (Registers 0x00 - 0x15)

Traces
```
2026-05-01 07:23:51.415  00000d7a  01 04 00 00 00 15 31 C5                          |......1.|
2026-05-01 07:23:51.503  00000d82  01 04 00 00 44 58 32 33 31 39 47 30 30 30 20 20  |....DX2319G000  |
2026-05-01 07:23:51.503  00000d92  20 20 20 20 20 20 20 20 00 00 00 AB 00 B2 00 AD  |        ........|
2026-05-01 07:23:51.503  00000da2  00 A5 00 A9 00 01 00 08 00 00 00 00 00 00 42 E3  |..............B.| 
- - - 
2026-05-01 07:24:30.771  00003981  02 04 00 00 00 15 31 F6                          |......1.|
2026-05-01 07:24:30.858  00003989  02 04 00 00 44 58 32 33 31 39 47 30 30 30 20 20  |....DX2319G000  |
2026-05-01 07:24:30.858  00003999  20 20 20 20 20 20 20 20 00 00 00 A2 00 A5 00 A6  |        ........|
2026-05-01 07:24:30.858  000039a9  00 96 00 A6 00 01 00 08 00 00 00 00 00 00 CD 31
```

| Range | Example Raw Value(s) | Interpretation |
|-|-|-|
|0-9 (10)|58 32 33 31 39 47 30 30 30 20 20 20 20 20 20 20 20 20 20 00|Serial Number (=DX2319G000)|
|10 - 11|00 00|Unknown|
|12 - 13|00 AB|Unknown (=171) Temp? 17.1C|
|14 - 15|00 B2|Unknown (=178)|
|16 - 17|00 AD|Unknown (=173)|
|18 - 19|00 A5|Unknown (=165)|
|20 - 21|00 A9|Unknown (=169)|
|22 - 23|00 01|Unknown (=1)|
|24 - 25|00 08|Unknown (=8)|
|26 - 27|00 00|Unknown|
|28 - 29|00 00|Unknown|
|30 - 31|00 00|Unknown|

### Block 2 (Registers 0x15 - 0x27)

Traces
```
2026-05-01 07:24:05.814  00001d99  01 04 00 15 00 13 A0 03                          |........|
2026-05-01 07:24:05.898  00001da1  01 04 00 15 10 02 E1 00 00 CD 33 CF 85 FF FF FF  |..........3.....|
2026-05-01 07:24:05.898  00001db1  35 00 00 4B C0 00 00 48 A8 00 00 46 7B 5D 00 00  |5..K...H...F{]..|
2026-05-01 07:24:05.898  00001dc1  0E 10 00 00 00 00 00 0B CE 00 7C 51
- - - 
2026-05-01 07:24:51.409  00005095  02 04 00 15 00 13 A0 30                          |.......0|
2026-05-01 07:24:51.493  0000509d  02 04 00 15 10 02 93 00 00 CF 87 D0 72 FF FF FF  |............r...|
2026-05-01 07:24:51.493  000050ad  3B 00 00 4C 9A 00 00 48 A8 00 00 44 10 59 00 00  |;..L...H...D.Y..|
2026-05-01 07:24:51.493  000050bd  0E 10 00 00 00 00 00 0B CE 00 9C F6 
```

| Range | Example Raw Value(s) | Interpretation |
|-|-|-|
|0|10|Num Cells (10 = 16)|
|1 - 2|02 E1|Num Battery Cycles (0293 = 737)|
|3 - 4|00 00|Unknown|
|5 - 6|CD 33|Unknown (=52531) 52.531V??|
|7 - 8|CF 85|Batt Voltage? (=53125) 53.125V??|
|9 - 14|FF FF FF 35 00 00|Unknown|
|15 - 16|4B C0|Battery Capacity (=193.92Ah)|
|17 - 18|00 00|Unknown|
|19 - 20|48 A8|Design Capacity (=186.00Ah)|
|21 - 22|00 00|Unknown|
|23 - 24|46 7B|Remaining Capacity (=180.43Ah)|
|25 - 25|5D|State Of Charge (5D=93%)|
|26 - 27|00 00||
|28 - 29|0E 10|(=3600)|
|30 - 34|00 00 00 00 00||
|35 - 36|0B CE|Firmware Version (0BCE=3022)|
|37| 00|Unknown|

### Block 3 (Registers 0x28-0x3C)
| Range | Example Raw Value(s) | Interpretation |
|-|-|-|
|0 - 31 (32)|0D 07 ...|Cell Voltage in milli-volts (0D 07 = 3335 mV).|
|32 - 33|00 A5 / 00 B3|Unknown (A5 = 165)|
|34 - 35|00 96 / 00 A5|Unknown (96 = 150)|
|36 - 37|0D 09|Max cell voltage (0D 09 = 3337 mV)|
|38 - 39|0D 05|Min cell voltage (0D 05 = 3333 mV)|

## Tooling

### Hardware
I'm using a [Waveshare USB to RS485 dongle](https://www.waveshare.com/usb-to-rs485.htm) to monitor the protocol, using Ethernet cable screwed into the Inverter's BMS terminal block along side the BMS cable.

### Software
See [serial_hexdump_logger.c](./serial_hexdump_logger.c) for a utility that will log all RS485 traffic seen to a log file (with timestamps).

---

## Firmware Static Analysis

> Contributed: complements the empirical RS485 captures above with findings from static analysis of the official BMS firmware binary. Cross-referenced against Ken's traces - every register Ken named or measured is consistent with what the firmware does, and several of his "Unknown" entries can be explained from the code.

GivEnergy's official BMS firmware is distributed as `BMS_ARM.bin` (one file per firmware version). These are STM32-family ARM Cortex-M Thumb binaries with a 4-byte vendor header (`0xVVVV 0x5566` - version little-endian + magic) followed by a standard Cortex-M vector table at file offset 4.

Analysis below is from firmware version **3022** (`0x0BCE`), which is the version Ken's first trace shows. Cross-checked against versions 3017 and 3020:
- **3020 is functionally identical to 3022** for the wire protocol; addresses shift but field meanings don't change.
- **3017 underwent a substantial refactor** between 3017 -> 3020 (143 KB -> 119 KB binary). Same fields, different code structure.
- **Gen 3 / 4xxx (~165 KB)** is a different protocol architecture and not analysed here.

### MCU and peripherals

- **MCU**: STM32F103xC/D/E or STM32F105/F107 connectivity-line variant. Determined from peripheral-base references in literal pools (GPIOA-D, USART1-3, **UART4 + UART5**, ADC1/2, SPI1) - UART4/5 don't exist on the basic STM32F103C8T6 used in the inverter.
- **Inverter Modbus port** is USART3 (`0x40004800`); 9600 baud is consistent with the firmware though the actual BRR write goes through helper functions.
- **Inter-pack channel** is on UART4 (`0x40004C00`), running a Pylontech-compatible PACE protocol (version `0x25`, CID1=`0x46` LiFePO4) for communication between paralleled batteries. Not relevant to inverter<->battery emulation but useful context.
- The bit-banged I2C on PB6/PB7 is just a `0x50` 24Cxx EEPROM (calibration / serial / lifetime counters), not a cell-monitor AFE.

### Modbus implementation (matches Ken's empirical findings)

The firmware's Modbus dispatcher (function entry at flash `0x0800e1b8`) implements **FC03, FC04 and FC06 only**. Any other function code returns exception 0x80|FC with exception code 1 ("Illegal Function"). Maximum register count per FC03 / FC04 read is `0x80` (128) - exceeded counts return exception code 2 / 4.

Frame buffer is at SRAM `0x2000385c`, layout: `[slave, FC, addr_hi, addr_lo, count_hi, count_lo]`. CRC handling is standard Modbus-RTU.

This confirms Ken's empirical observation that the inverter only ever does reads - FC06 is in the firmware but not part of the inverter's normal poll cycle.

### FC03 backing store (holding-register table)

The firmware's holding registers are a flat array of **200 (`0xC8`) 16-bit halfwords** at SRAM `0x200039C0`. The FC03 handler is essentially:
```
for i in range(start, start+count):
    tx.append(htons(table[i]))
```
No per-register handler logic - it's just a SRAM mirror that other tasks populate.

- The init function at `0x0800d534` clears all 200 registers to `0xFFFF`, then writes specific defaults.
- The update function at `0x0800d584` recomputes volatile fields each cycle.

This is how the values Ken observes in the HR poll get there.

### Holding-register interpretations (firmware-derived)

Same byte ranges as Ken's HR table above; this fills in many of his "Unknown" entries based on what the firmware code does. Confidence varies - some are direct (e.g. firmware version is literally `movw r0, #0xbce`), others are inferences from helper-function context.

| Reg | Bytes | Ken's empirical | Firmware-derived |
|-----|-------|-----------------|------------------|
| 0   | 0-1   | `0x0065`=101 (constant) | Init writes literal `0x65` once; persistent. Likely a **fixed protocol/device marker** (not the slave address - that's set by dipswitches) |
| 1-4 | 2-9   | `FF FF` × 4 | Never written by firmware after the 0xFFFF init - **truly unused** |
| 5-9 | 10-19 | Serial Number | Confirmed: 5 halfwords copied big-endian from a 10-byte SRAM struct |
| 10  | 20-21 | `FF FF` | Never written - **unused** |
| 11  | 22-23 | varies (Unknown) | Computed via `bl 0x801c2ac; bl 0x801c468`. The increment pattern across Ken's traces (186 -> 372 over minutes) suggests an **accumulator/counter** rather than a voltage |
| 12  | 24-25 | `0x0030`=48 | Init writes literal `0x30`; constant. Possibly a **hardware revision** field |
| 13  | 26-27 | `0x0BCE`=3022 - Firmware Version | Confirmed exactly: `movw r0, #0xbce; strh r0, [r4, #0x1a]` |
| 14  | 28-29 | (Unknown) | Set to 0 or 1 based on a flag byte - **boolean status** (charge/discharge active? balancing?) |
| 15  | 30-31 | (Unknown) | OR-mask of 3 conditional bits (#1, #2, #4) - **3-flag composite status** |
| 16  | 32-33 | (Unknown) | Single byte loaded from RAM - **mode/state byte** |
| 17  | 34-35 | varies | Low 16 bits of a 32-bit value built from 6 bytes via `bl 0x80011dc` (digit-decoder) - **half of an encoded production ID/hash** |
| 18  | 36-37 | `0x389D`=14493 | High 16 bits of the same 32-bit value. **17 + 18 are one logical 32-bit field** |
| 19  | 38-39 | varies | OR-mask of 8 conditional bits (`#1, #2, #4, #8, #0x10, #0x20, #0x40, #0x80`) - **8-flag fault/warning composite** |
| 23  | 46-47 | `0xFFF2`/`0x004E` - Ken: "Current?" | Likely correct: `0xFFF2` = -14 as signed int16, `0x004E` = +78. Sign flips across samples = direction reversal. **Signed pack current**, probably in 0.01 A units |
| 25  | 50-51 | `0x2328`=9000 (Unknown) | Written via `(raw << 4) & 0x3FFFC` at flash `0x0800d78c`. 9000 in 0.01 A units = **90.00 A - likely the continuous discharge current limit** |

Regs 20, 21, 22, 24, 26, 27 are written somewhere outside the init/update functions found so far; static analysis didn't pin them down without more work.

### Cell voltages on FC04 - resolves a potential ambiguity

The firmware's FC04 handler at `0x0800debc` populates responses from a per-pack 145-byte structure at SRAM `0x20003D6A` (one structure per pack; up to 6 packs supported, indexed by slave_address - 1).

Inside that handler, one code path stores cell voltages with a `-2730` offset (`0xAAA` = 2730 mV = LiFePO4 lower-cutoff baseline). This led to an initial misconception that cell voltages on the wire would be offset. **Ken's empirical Block 3 readings (e.g. `0D 07 = 3335 mV`) confirm that the values reaching the inverter on FC04 are raw millivolts**, not offset. The `-2730` path is likely a leftover from internal storage or the inter-pack PACE encoding. An emulator can send raw mV, big-endian, with no transform.

### Implications for emulator implementation

For a slave that pretends to be a GivEnergy battery on the inverter's RS485 bus:

1. **9600 baud, 8N1, standard Modbus-RTU** - confirmed both empirically and from firmware.
2. **Implement FC03 + FC04 only** - FC06 is in the firmware but not part of the inverter's poll cycle.
3. **Slave address = battery position** (1, 2, ...), set by dipswitches on real units.
4. **HR response**: 28 registers (`0x1C`), 56-byte payload, layout per Ken's HR table.
5. **IR responses**: three blocks (`0x00..0x14`, `0x15..0x27`, `0x28..0x3C`).
6. **Cell voltages**: raw millivolts, big-endian, IR Block 3 starting at byte 0.
7. **CRC**: standard Modbus-RTU CRC16.

Bitmask fields (regs 14, 15, 19) and varying counters (reg 11) can probably be set to plausible static defaults initially; deciding which bits the inverter actually validates would need experiments against a real inverter.

### Reproducing the firmware analysis

The firmware binaries are distributed by GivEnergy as part of their "BMS update" tooling. Once obtained, basic analysis can be done with [Capstone](https://www.capstone-engine.org/) (Python bindings), Ghidra, or any STM32-aware disassembler. Useful entry points to start from:

- File offset 4 onwards - Cortex-M vector table, includes the reset vector
- Flash `0x0800e1b8` - Modbus FC dispatcher (CMP r0, #3 / #4 / #6 chain)
- Flash `0x0800dd82` - FC03 handler (the one that does `for i: tx.append(htons(table[i]))`)
- Flash `0x0800debc` - FC04 handler (populates the per-pack TX buffer)
- Flash `0x0800d534` - FC03 table init function (writes the constants Ken sees in unchanging fields)
- SRAM `0x200039C0` - start of the 200-register holding table backing store
- SRAM `0x2000385c` - Modbus RX/TX frame buffer