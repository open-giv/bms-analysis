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
|15 - 16|4B C0|Battery Capacity (=19392mAh)|
|17 - 18|00 00|Unknown|
|19 - 20|48 A8|Design Capacity (=18600mAh)|
|21 - 22|00 00|Unknown|
|23 - 24|46 7B|Remaining Capacity (=18043mAh)|
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