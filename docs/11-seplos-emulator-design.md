# Emulator design: Seplos BMS battery on a GivEnergy inverter

This document is a design for a concrete case of Goal 1 ([07-emulator-implications.md](07-emulator-implications.md)). A third-party LiFePO4 battery with a Seplos BMS replaces a GivEnergy battery on a GivEnergy G3 hybrid inverter. The target battery is the Fogstar Energy 48V 32 kWh, but the design applies to any 16S battery with a Seplos BMS v3.

> **Status (October 2026): superseded as a build plan.** This design read the Seplos BMS over its Modbus RTU port. My bridge now takes a different route: the pack talks Growatt LV CAN, which Battery-Emulator reads with its `GROWATT-LV-BATTERY` module, and a GivEnergy LV RS485 inverter module answers the G3. That module is public in [abedegno/Battery-Emulator#1](https://github.com/abedegno/Battery-Emulator/pull/1). It replies only once the battery has reported, waits 75 ms after each request before replying (as a real GivEnergy battery does; a G3 rejected replies sent after about 1 ms), caps both limits at 90 A, tapers HR26 by the highest cell (3.45 V to 3.55 V, down to a 3 A trickle, 0 at 3.60 V) and latches the trickle until the highest cell is below 3.40 V. It sets HR20 bit 2 on a fault, a zero charge limit from the battery, a cell at 3.60 V or the user's voltage ceiling, and bit 3 on a fault, a zero discharge limit or a cell at 2.90 V, and it has a guard against battery calibrations. The Seplos register map and the GivEnergy field mapping below still hold for anyone reading a Seplos over Modbus, and they are corrected against the official Seplos protocol document.

Status of this design: nothing here has been run against a real inverter. The [open questions](#open-questions) must be answered before connecting a real battery.

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

The Seplos can talk Pylontech-style CAN on its inverter port, but that protocol only carries the minimum and maximum cell voltage. GivEnergy IR Block 3 needs all 16 cell voltages. (Growatt LV CAN, the route my bridge now uses, does carry them: frames `0x315` to `0x318` hold cells 1 to 16, optional from protocol V1.03, and `0x319` holds the highest and lowest cell.) Seplos confirmed (October 2026) how its BMS uses them in Growatt mode:
- a standalone pack sends all 16 cells on `0x315` to `0x318`
- with packs in parallel those frames aren't sent, and `0x319`'s highest and lowest cell are calculated across all packs
- the inverter protocol register PCT `0x1800` reads 1 for Growatt CAN; Seplos's command to set it is `00 10 18 00 00 01 02 00 01 F2 01`

See the notes in esphome-seplos-bms ([PR #251](https://github.com/syssi/esphome-seplos-bms/pull/251)). Seplos BMS v3 exposes everything over Modbus RTU at 19200 baud, 8N1. Several open-source projects read it, e.g. [esphome-seplos-bms](https://github.com/syssi/esphome-seplos-bms) and [bms_connector](https://github.com/flip555/bms_connector). Seplos's own protocol document, "Modbus_RTU communication protocol" V0.3 (2024-10), with an English translation, is now in esphome-seplos-bms ([PR #247](https://github.com/syssi/esphome-seplos-bms/pull/247)). The register notes in [marcelrv/seplosBMSv3](https://github.com/marcelrv/seplosBMSv3) come from the Seplos app and its traffic, and agree with it. Those notes are licensed CC BY-NC, so this document summarises and links them instead of copying them.

The emulator reads two blocks with FC=4 (read input registers):

| Seplos block | Start | Count | Contents used here |
|---|---|---:|---|
| PIA | `0x1000` | 18 | Pack voltage (0.01 V), current (signed, positive = charging; 0.1 A on 300 A and 400 A boards, 0.01 A on boards of 200 A and below), remaining and total capacity (0.01 Ah), SoC (0.1%), SoH (0.1%), cycles, max and min cell voltage and temperature, max discharge current (`0x100F`) and max charge current (`0x1010`), both in 1 A |
| PIB | `0x1100` | 26 | 16 cell voltages (mV), cell temperatures 1 to 4 (`0x1110` to `0x1113`; `0x1114` to `0x1117` are reserved), ambient and power temperatures |

The current unit depends on the board, so check the board's current rating before mapping it. The Fogstar 32 kWh has a 300 A board, so its PIA current is in 0.1 A.

Three more blocks are useful but not needed for a first version. PIC at `0x1200` (144 coils, read with FC=1) holds per-cell voltage and per-sensor temperature alarms, balancing flags, the system state, event bytes, FET state and hardware faults. It feeds HR20 and the protection bit of HR19. SPA at `0x1300` (settings, read with FC=4) holds the protection thresholds, including the float lock at 3.50 V per cell, its release at 3.40 V and its lock current of 1.5 A (`0x1328` to `0x132A`), and the charge request voltage and charge and discharge request currents the BMS sends to an inverter (`0x1365` to `0x1367`). Those are the document's example values; read them from the real pack. VIA at `0x1700` holds the manufacturer and serial strings.

Temperatures in these blocks are 0.1 K, stored as `decidegC + 2731`, as the Seplos document states. The emulator converts with `decidegC = raw - 2731`.

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
| HR13 | `0x0BCE` (3022) | Constant. Claims a known GivEnergy BMS firmware. It must be 3011 or higher: below that, a G3 LV takes both current limits from HR25 and ignores HR26 and HR27. |
| HR14 to HR16 | `0x0000` | Constant |
| HR17, HR18 | HR17 +1 each second, HR18 constant | Clock-derived on a real BMS; see [open questions](#open-questions) |
| HR19 | Status bits | Built from Seplos state, see [HR19](#hr19) |
| HR20 | Alarm bits | `0x0000` normally. Bit 2 at a charge stop (it zeroes the G3's charge limit at once) and bit 3 at a discharge stop (it cuts the discharge limit to 10%); see [07-emulator-implications.md](07-emulator-implications.md#stopping-a-charge-on-a-g3-lv) |
| HR21 | SoC % | PIA SoC / 10 |
| HR22 | Pack voltage, 0.01 V | PIA voltage, same units |
| HR23 | Pack current, 0.01 A, positive = charge | PIA current. Same sign (positive = charging). Multiply by 10 on a 300 A or 400 A board; same units on a board of 200 A or below |
| HR24 | Max temperature, whole °C | (PIA max cell temperature - 2731) / 10 |
| HR25 | Configured max charge current x 100 | A fixed value chosen for the install |
| HR26 | Charge current limit, 0.01 A | min(PIA recommended max charge current, install cap) x 100 |
| HR27 | Discharge current limit, 0.01 A | min(PIA recommended max discharge current, install cap) x 100 |

HR26 and HR27 are the main safety controls, because the inverter honours them (see [02-holding-registers.md](02-holding-registers.md)). A G3 LV uses HR26 as the charge limit and HR27 as the discharge limit, as the table says (see the G3 LV note in `docs/02` and question 10). Passing the Seplos's max currents through lets the Seplos lower them if it wants to. How far it lowers them near full or empty isn't documented, so the emulator should also taper HR26 by the highest cell voltage itself (see [07-emulator-implications.md](07-emulator-implications.md#stopping-a-charge-on-a-g3-lv)). The install cap is a value the installer sets, e.g. the inverter's own battery current rating.

### HR19

The bit meanings come from `HR19_BITS` in `tools/decode_fields.py`. Only bits 0, 1 and 3 have been checked against wire data. The emulator sends the value that a healthy GivEnergy battery sends for every other bit. What a G3 LV's DSP does with each bit is in [05-inverter-firmware.md](05-inverter-firmware.md#what-the-dsp-does-with-the-bms-status-registers): bit 2 clear forces a charge of at least 300 W, bits 0 and 1 both clear mean "idle" and skip the BMS limits, bit 3 has no reader, bit 4 is status only, and bit 5 asks for a small forced discharge.

| Bit | Name | Value to send |
|---:|---|---|
| 0 | `discharging_or_idle` | 1 unless current > 0 |
| 1 | `current_flowing` | 1 unless current = 0 |
| 2 | `charge_vote_ok` | 1, as seen on every poll in the G3 capture |
| 3 | `all_cells_ok` | 1 unless the Seplos reports a cell undervoltage alarm, or the lowest cell is below a threshold at rest. A G3 LV ignores it |
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
| Block 2, capacities | 0.01 Ah | PIA total and remaining capacity, same units. Each field is 32 bits on the wire (see [03-input-registers.md](03-input-registers.md)), and a G3 keeps values below 2000.00 Ah, so 628 Ah fits. |
| Block 2, SoC | % | PIA SoC / 10 |
| Block 2, firmware version | 3022 | Constant, matches HR13 |
| Block 3, 16 cells | mV | PIB cells 1 to 16, same units |
| Block 3, max and min temperature | 0.1 °C, signed | PIA max and min cell temperature, minus 2731 |
| Block 3, max and min cell voltage | mV | PIA max and min cell voltage, same units |

The inverter stops discharging when the SoC in HR21 reaches its 4% floor; the G3 LV DSP takes its SoC from HR21, not from IR Block 2 (see [07-emulator-implications.md](07-emulator-implications.md#common-pitfalls)). Send the same SoC in both. With the Seplos SoC passed through unchanged, the floor is 4% of 628 Ah, about 25 Ah. The emulator can rescale SoC if a larger reserve is wanted, e.g. report `(SoC - 10) / 0.9` so that the inverter's 4% sits at about 14% real SoC.

## Timing

The two sides run separately. One task polls the Seplos about once a second and stores the latest decoded values with a timestamp. The other task answers the inverter from those stored values.

- The inverter polls HR every 240 ms and expects an answer in about 100 ms (see [06-wire-captures.md](06-wire-captures.md)). The answering task must never wait on a Seplos read.
- IR Block 1 comes twice (10 s apart) per device in every ~200 s sweep, and Blocks 2 and 3 once per sweep. Any Seplos poll rate of 1 Hz or faster keeps them fresh.

## Failure behaviour

A stale answer is more dangerous than no answer. If the stored Seplos values are older than a set limit, e.g. 10 s, the emulator stops answering the inverter. This way the inverter never charges a pack that is already full because the emulator kept repeating an old SoC and current limit. What the G3 does when its battery stops answering is one of the [open questions](#open-questions).

The emulator also sets HR26 and HR27 to 0 when:

- The Seplos reports a protection state (HR19 bit 4). Set HR20 bits 2 and 3 as well.
- Any cell is at or above the charge limit chosen for the install. HR26 only, with HR20 bit 2: HR26 = 0 alone still lets about 1 A through for 30 s.
- Any cell is at or below the discharge limit chosen for the install. HR27 only, with HR20 bit 3: HR27 = 0 alone still lets about 2 A through.

The Seplos BMS keeps its own protections whatever the emulator does. It opens its MOSFETs at its own limits. The emulator's limits should trigger before the Seplos limits, so that the Seplos cut-off is a backstop and not the normal way to stop.

## Hardware

The emulator needs two RS485 ports: one to the Seplos and one to the inverter.

- **Raspberry Pi with two USB RS485 dongles.** The Pi 3 B+ ordered for capture work can run this. An isolated dongle, e.g. the Waveshare, is recommended on the inverter side.
- **ESP32 board with two RS485 transceivers.** Issue #15 describes a LilyGo ESP32 RS485 emulator running on a Gen 1 inverter. An ESP32 starts faster than a Pi after a power cut and has no SD card to wear out.

Either way, the emulator must be powered from a supply that stays up when the inverter is running on battery.

## Open questions

These must be answered before a real Seplos battery is connected to the inverter. Most can be answered with captures from a G3 running its original GivEnergy battery (see [06-wire-captures.md](06-wire-captures.md#capture-experiments-worth-running)).

1. **Charge voltage.** Answered (September 2026). A G3 LV never takes a charge voltage from the battery. It measures the pack itself and charges until the battery lowers HR26, apart from its own taper by SoC from 90%. Its over-voltage trip comes from the inverter setting HR98 (maximum + 1.0 V for 1 s, or + 2.0 V for 40 ms, on the inverter's own reading), not from the battery. So the emulator sets where the pack stops: it must cut HR26 as the pack approaches full, set HR20 bit 2 at its stop, and keep the pack's ceiling far enough below the HR98 trip. My capture of a full charge with a GivEnergy battery shows what the real BMS does (see [06-wire-captures.md](06-wire-captures.md#findings-from-my-g3-capture-september-2026)). The details are in [05-inverter-firmware.md](05-inverter-firmware.md#battery-voltage-checks) and [07-emulator-implications.md](07-emulator-implications.md#stopping-a-charge-on-a-g3-lv).
2. **Large single-pack capacity.** Answered by firmware analysis (September 2026), not yet on the wire. A G3 LV has no table of battery models and no capacity check. The DSP clamps HR11 at 10000 Ah, and the ARM keeps each IR Block 2 capacity up to 2000.00 Ah, so 628 Ah passes unchanged. HR11 then scales the HR111/HR112 current caps (capacity x % + 1.5 A) and the forced charge and discharge power, and the inverter reports it back as HR55 (GivTCP's battery capacity). GivEnergy packs report 160 Ah (8.2 kWh) or 186 Ah (9.5 kWh) each. Never send HR11 = 0, which drops the caps to 1.5 A and 2 A. Presenting several virtual packs on devices 1 to 4 is no longer needed for capacity.
3. **Seplos port for the reader.** The sources confirm Modbus RTU at 19200 baud but don't say which physical port an external reader should use: the inverter port in RS485 mode, or a link port. With more than one pack, the host BMS is the master on the link bus, which affects this.
4. **Seplos current sign and temperature offset.** The Seplos document gives positive current as charging, temperatures as 0.1 K (subtract 2731), and the current unit as 0.1 A on 300 A and 400 A boards and 0.01 A on smaller ones. Still check the current against a clamp meter and the temperatures against a thermometer before trusting the mapping.
5. **Startup check on a G3.** A Gen 1 inverter needs 7 good replies in a row before it accepts a battery (issue #15). Firmware analysis of the G3 LV shows no such debounce on either chip: the first reply with the right length and CRC marks the battery present and connected (see [05-inverter-firmware.md](05-inverter-firmware.md#a316-the-dsp-runs-the-bms-bus)). Nobody has captured a G3 cold boot yet to confirm it.
6. **Battery lost.** Firmware analysis of the G3 LV DSP shows that after about 30 seconds without a valid reply it zeroes the charge and discharge limits and the SoC it holds, and raises a comms fault. The emulator's own stale-data cut-off (10 s above) triggers well before that. This hasn't been seen on the wire yet.
7. **Serial and HR17/HR18.** Does the inverter check the battery serial or the HR17/HR18 values? HR17 turns out to be clock-derived: in the G3 capture it changed once per second, mostly by +1 (see [02-holding-registers.md](02-holding-registers.md)), so the emulator should tick it once per second. dobberzzr's emulator used a GivEnergy-style serial (`DX2319G000`) and was accepted on a Gen 1.
8. **HR20 alarm mapping.** Partly answered by firmware analysis. Only bits 2 and 3 change what a G3 LV does: bit 2 zeroes the charge limit at once outside a calibration, and bit 3 cuts the discharge limit to 10% of rated power. During a calibration they mark "full" and "empty" for the inverter. Which Seplos alarms should set them is still a design choice, and whether the app shows them as alarms is open.
9. **Inverter current rating.** Answered for my G3 5 kW (26 to 29 September 2026, GivTCP data): charging plateaued at 64.7 A to 65.0 A, a steady battery power of about 3,490 W (the inverter's 3600 W battery power limit, not a current cap), and discharge peaked at 70.7 A. The G3 3.6 kW peaked at 76 A discharging and 65 A charging in the 90-hour capture.
10. **HR26 and HR27 roles on a G3 LV.** Settled: a capture from a G3 LV shows HR26 is the charge limit and HR27 the discharge limit, as `docs/02` says (see its G3 LV note). An earlier reading of the DSP firmware suggested the opposite.

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
