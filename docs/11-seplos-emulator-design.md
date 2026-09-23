# Emulator design: Seplos BMS battery on a GivEnergy inverter

This document is a design for a concrete case of Goal 1 ([07-emulator-implications.md](07-emulator-implications.md)). A third-party LiFePO4 battery with a Seplos BMS replaces a GivEnergy battery on a GivEnergy G3 hybrid inverter. The target battery is the Fogstar Energy 48V 32 kWh, but the design applies to any 16S battery with a Seplos BMS v3.

Status: design only. Nothing here has been run against a real inverter yet. The [open questions](#open-questions) must be answered before connecting a real battery.

```
[Seplos BMS] <-- RS485, Modbus RTU, 19200 --> [Emulator] <-- RS485, GivEnergy Modbus, 9600 --> [G3 inverter]
               (emulator is the client)                     (emulator is the BMS, device 1..5)
```

## Target battery: Fogstar Energy 48V 32 kWh

From the [product page](https://www.fogstar.co.uk/collections/solar-battery-storage/products/fogstar-energy-48v-32kwh-battery-heating-fire-supression) and the [user manual](https://cdn.shopify.com/s/files/1/1347/0997/files/51.2V_32KWH_USER_MANUAL_2026_FireSupression_Heating_458852e0-bb80-4158-b79d-01c60d96ca3f.pdf?v=1782471745):

| Item | Value |
|---|---|
| Cells | LiFePO4, 16S2P |
| Voltage | 51.2 V nominal, 57.6 V maximum charge, 43.2 V discharge cut-off |
| Capacity | 628 Ah, 32.15 kWh |
| BMS | Seplos, 300 A, with a JK 2 A active balancer |
| Temperature sensors | 4 |
| Inverter port | RJ45 with CAN and RS485. Pins 1 and 8 are RS485-B, 2 and 7 are RS485-A, 4 is CAN-H, 5 is CAN-L, 3 and 6 are GND. |
| Link ports | RS485A and RS485B at 19200 baud, for chaining packs. Automatic addressing, no DIP switches. |
| Built-in inverter protocols | Victron, Sunsynk, Solis, LuxPower and others. GivEnergy is not listed. |

The 16 cells in series match the 16 cells that GivEnergy's IR Block 3 carries, so cell data maps across without regrouping.

## Why read Seplos Modbus rather than CAN

The Seplos can talk Pylontech-style CAN on its inverter port, but that protocol only carries the minimum and maximum cell voltage. GivEnergy IR Block 3 needs all 16 cell voltages. Seplos BMS v3 exposes everything over Modbus RTU at 19200 baud, 8N1. Several open-source projects read it, e.g. [esphome-seplos-bms](https://github.com/syssi/esphome-seplos-bms) and [bms_connector](https://github.com/flip555/bms_connector). The register notes in [marcelrv/seplosBMSv3](https://github.com/marcelrv/seplosBMSv3) come from the Seplos app and its traffic. Those notes are licensed CC BY-NC, so this document summarises and links them instead of copying them.

The emulator reads two blocks with FC=4 (read input registers):

| Seplos block | Start | Count | Contents used here |
|---|---|---:|---|
| PIA | `0x1000` | 18 | Pack voltage (0.01 V), current (0.01 A, signed), remaining and total capacity (0.01 Ah), SoC (0.1%), SoH, cycles, max and min cell voltage and temperature, recommended max charge and discharge current (A) |
| PIB | `0x1100` | 26 | 16 cell voltages (mV), cell temperatures 1 to 8, ambient and power temperatures |

Two more blocks are useful but not needed for a first version. PIC at `0x1200` (144 alarm bits, read as coils) feeds HR20 and the protection bit of HR19. VIA at `0x1700` holds the manufacturer and serial strings.

Temperatures in these blocks are 0.1 K, stored as `decidegC + 2731`. The example responses in the marcelrv notes show cell temperatures of 2831 to 2845, which would be 10.0 to 11.4 °C, and an ambient reading of exactly 2731, which would be 0 °C. The emulator converts with `decidegC = raw - 2731`. This offset is inferred from example values and must be checked on the real BMS.

## Field mapping

Every value below comes from one Seplos read of PIA and PIB. GivEnergy register meanings are from [02-holding-registers.md](02-holding-registers.md) and [03-input-registers.md](03-input-registers.md).

### Holding registers (device 1, FC=3, 28 registers)

| GivEnergy | Value to send | Source |
|---|---|---|
| HR0 | `0x0065` | Constant |
| HR1 to HR4 | `0xFFFF` | Constant |
| HR5 to HR9 | 10-character ASCII serial | Synthetic, see [open questions](#open-questions) |
| HR10 | `0xFFFF` | Constant |
| HR11 | Capacity in whole Ah | PIA total capacity / 100. 628 for this battery. |
| HR12 | `0x0030` | Constant |
| HR13 | `0x0BCE` (3022) | Constant. Claims a known GivEnergy BMS firmware. |
| HR14 to HR16 | `0x0000` | Constant |
| HR17, HR18 | HR17 +1 each second, HR18 constant | Clock-derived on a real BMS; see [open questions](#open-questions) |
| HR19 | Status bits | Built from Seplos state, see [HR19](#hr19) |
| HR20 | Alarm bits | `0x0000` unless a Seplos alarm maps to a documented HR20 bit |
| HR21 | SoC % | PIA SoC / 10 |
| HR22 | Pack voltage, 0.01 V | PIA voltage, same units |
| HR23 | Pack current, 0.01 A, positive = charge | PIA current, same units. The Seplos sign convention must be checked. |
| HR24 | Max temperature, whole °C | (PIA max cell temperature - 2731) / 10 |
| HR25 | Configured max charge current x 100 | A fixed value chosen for the install |
| HR26 | Charge current limit, 0.01 A | min(PIA recommended max charge current, install cap) x 100 |
| HR27 | Discharge current limit, 0.01 A | min(PIA recommended max discharge current, install cap) x 100 |

HR26 and HR27 are the main safety controls, because the inverter honours them (see [02-holding-registers.md](02-holding-registers.md)). The Seplos lowers its recommended currents as the pack approaches full or empty, so passing them through lets the Seplos taper the charge. The install cap is a value the installer sets, e.g. the inverter's own battery current rating.

### HR19

The bit meanings come from `HR19_BITS` in `tools/decode_fields.py`. Only bits 0, 1 and 3 have been checked against wire data. The emulator sends the value that a healthy GivEnergy battery sends for every other bit.

| Bit | Name | Value to send |
|---:|---|---|
| 0 | `discharging_or_idle` | 1 unless current > 0 |
| 1 | `current_flowing` | 1 unless current = 0 |
| 2 | `charge_vote_ok` | 1, as seen on every poll in the G3 capture |
| 3 | `all_cells_ok` | 1 unless the Seplos reports a cell undervoltage alarm, or the lowest cell is below a threshold at rest |
| 4 | `protection_active` | 1 if the Seplos reports any protection state, else 0 |
| 5 | `discharge_vote` | 0 |
| 6 | `allow_discharge` | 1 |
| 7 | `allow_charge_and_discharge` | 1 |

A healthy discharging pack gives 207 (`0xCF`) and a healthy charging pack gives 206 (`0xCE`), the same values the real G3 capture shows.

### Input registers (FC=4, devices 1 to 5)

Device 1 carries the Seplos data. Devices 2 to 5 return the absent-device pattern from [03-input-registers.md](03-input-registers.md), unless the design splits the battery across several virtual packs (see [open questions](#open-questions)).

| GivEnergy | Value to send | Source |
|---|---|---|
| Block 1, bytes 0 to 19 | Serial, 20 bytes ASCII | Same synthetic serial as HR5 to HR9 |
| Block 1, 5 temperatures | 0.1 °C, signed | PIB cell temperatures 1 to 4, minus 2731. Repeat the highest reading in the fifth slot. |
| Block 2, cell count | 16 | Constant |
| Block 2, cycles | Count | PIA cycles |
| Block 2, pack voltage | mV | PIA voltage x 10 |
| Block 2, capacities | 0.01 Ah | PIA total and remaining capacity, same units. 628 Ah is 62,800, which fits in 16 bits. |
| Block 2, SoC | % | PIA SoC / 10 |
| Block 2, firmware version | 3022 | Constant, matches HR13 |
| Block 3, 16 cells | mV | PIB cells 1 to 16, same units |
| Block 3, max and min temperature | 0.1 °C, signed | PIA max and min cell temperature, minus 2731 |
| Block 3, max and min cell voltage | mV | PIA max and min cell voltage, same units |

The inverter stops discharging when the Block 2 SoC reaches its 4% floor (see [06-wire-captures.md](06-wire-captures.md#discharge-stops-at-the-4-soc-floor)). With the Seplos SoC passed through unchanged, the floor is 4% of 628 Ah, about 25 Ah. The emulator can rescale SoC if a larger reserve is wanted, e.g. report `(SoC - 10) / 0.9` so that the inverter's 4% sits at about 14% real SoC.

## Timing

The two sides run separately. One task polls the Seplos about once a second and stores the latest decoded values with a timestamp. The other task answers the inverter from those stored values.

- The inverter polls HR every 240 ms and expects an answer in about 100 ms (see [06-wire-captures.md](06-wire-captures.md)). The answering task must never wait on a Seplos read.
- IR Block 1 comes about every 10.5 s per device, and Blocks 2 and 3 about every 200 s. Any Seplos poll rate of 1 Hz or faster keeps them fresh.

## Failure behaviour

A stale answer is more dangerous than no answer. If the stored Seplos values are older than a set limit, e.g. 10 s, the emulator stops answering the inverter. This way the inverter never charges a pack that is already full because the emulator kept repeating an old SoC and current limit. What the G3 does when its battery stops answering is one of the [open questions](#open-questions).

The emulator also sets HR26 and HR27 to 0 when:

- The Seplos reports a protection state (HR19 bit 4).
- Any cell is at or above the charge limit chosen for the install. HR26 only.
- Any cell is at or below the discharge limit chosen for the install. HR27 only.

The Seplos BMS keeps its own protections whatever the emulator does. It opens its MOSFETs at its own limits. The emulator's limits should trigger before the Seplos limits, so that the Seplos cut-off is a backstop and not the normal way to stop.

## Hardware

The emulator needs two RS485 ports: one to the Seplos and one to the inverter.

- **Raspberry Pi with two USB RS485 dongles.** The Pi 3 B+ ordered for capture work can run this. An isolated dongle, e.g. the Waveshare, is recommended on the inverter side.
- **ESP32 board with two RS485 transceivers.** Issue #15 describes a LilyGo ESP32 RS485 emulator running on a Gen 1 inverter. An ESP32 starts faster than a Pi after a power cut and has no SD card to wear out.

Either way, the emulator must be powered from a supply that stays up when the inverter is running on battery.

## Open questions

These must be answered before a real Seplos battery is connected to the inverter. Most can be answered with captures from a G3 running its original GivEnergy battery (see [06-wire-captures.md](06-wire-captures.md#capture-experiments-worth-running)).

1. **Charge voltage.** None of the mapped registers sets the voltage the inverter charges to. What voltage does a G3 hold near full charge with a GivEnergy battery, and does it stay below the Seplos limits for this pack? Capture the end of a full charge and record HR22, HR26 and the inverter's reported battery voltage.
2. **Large single-pack capacity.** Does a G3 accept one pack that reports 628 Ah? GivEnergy packs report 186 Ah each. The fallback is to present the battery as several virtual packs on devices 1 to 4, dividing the capacity between them. HR23 then carries the current per pack (see HR23 in [02-holding-registers.md](02-holding-registers.md)).
3. **Seplos port for the reader.** The sources confirm Modbus RTU at 19200 baud but don't say which physical port an external reader should use: the inverter port in RS485 mode, or a link port. With more than one pack, the host BMS is the master on the link bus, which affects this.
4. **Seplos current sign and temperature offset.** Check the current sign (positive for charge or discharge) and the 2731 temperature offset against a clamp meter and a thermometer before trusting the mapping.
5. **Startup check on a G3.** A Gen 1 inverter needs 7 good replies in a row before it accepts a battery (issue #15). Nobody has captured a G3 cold boot yet.
6. **Battery lost.** What does a G3 do when device 1 stops answering? It should stop using the battery and raise a fault, but this hasn't been seen on the wire.
7. **Serial and HR17/HR18.** Does the inverter check the battery serial or the HR17/HR18 values? HR17 turns out to be clock-derived: in the G3 capture it changed once per second, mostly by +1 (see [02-holding-registers.md](02-holding-registers.md)), so the emulator should tick it once per second. dobberzzr's emulator used a GivEnergy-style serial (`DX2319G000`) and was accepted on a Gen 1.
8. **HR20 alarm mapping.** Which Seplos alarms should set which HR20 bits, and how does the G3 react to each one?
9. **Inverter current rating.** What battery current does the G3 5 kW draw at full power? The G3 3.6 kW peaked at 76 A discharging and 65 A charging in the 90-hour capture.

## Test plan

1. **Capture the original system.** Record a G3 with its GivEnergy battery through a cold boot, a full charge, a full discharge to the 4% floor, and a forced charge and discharge. This answers questions 1, 5, 6 and 9.
2. **Read the Seplos on the bench.** Connect the emulator's Seplos port only. Log PIA and PIB for a day, and check the values against the Seplos PC software and a clamp meter. This answers questions 3 and 4.
3. **Replay against the emulator.** Feed recorded inverter requests from step 1 into the emulator's inverter port, and check every response against the field mapping above with the decoder in `tools/decode_fields.py`.
4. **Connect to the inverter with low limits.** Cap HR26 and HR27 at a low current, e.g. 10 A, and watch the inverter accept the battery, charge and discharge. Confirm that stopping the emulator makes the inverter stop using the battery. Raise the caps in steps.

## See also

- [07-emulator-implications.md](07-emulator-implications.md) - general emulator requirements this design builds on
- [02-holding-registers.md](02-holding-registers.md), [03-input-registers.md](03-input-registers.md) - GivEnergy register layouts
- [06-wire-captures.md](06-wire-captures.md) - G3 poll cadence, SoC floor, and capture methodology
- [marcelrv/seplosBMSv3](https://github.com/marcelrv/seplosBMSv3) - Seplos BMS v3 Modbus register notes
