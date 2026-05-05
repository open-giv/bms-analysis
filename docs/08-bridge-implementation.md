# Bridge implementation (GivEnergy battery -> third-party inverter)

Design rules and implementation guidance for a bridge that lets a GivEnergy LV battery work with a non-GivEnergy inverter. This is the dual of the emulator described in [07-emulator-implications.md](07-emulator-implications.md): instead of emulating the BMS toward an inverter, the bridge consumes the real BMS and re-presents it on the standard protocols third-party inverters expect.

```
[GivEnergy battery] <-- RS485 / GivEnergy Modbus --> [Bridge] <-- standard protocol --> [3rd-party inverter]
                          (bridge is controller)                       (bridge is device / talker)
```

## Bridge responsibilities

The bridge is a **two-sided protocol translator**:

1. **GivEnergy side (controller)**: poll the BMS using the same protocol the GivEnergy inverter uses. HR poll on device 1 every ~245 ms, IR rotation across devices 1..N every ~10 s. Standard FC=3 framing, non-standard FC=4 framing. See [01-protocol.md](01-protocol.md), [02-holding-registers.md](02-holding-registers.md), [03-input-registers.md](03-input-registers.md).

2. **Inverter side (device / talker)**: present the parsed BMS state in the format the third-party inverter expects. Common targets are listed below.

3. **Translation layer**: map GivEnergy register fields to the target protocol's fields, applying any unit conversions, range clamping, and time-averaging. Many target protocols expect slower update rates than GivEnergy provides (e.g. 1 Hz instead of 4 Hz), so light filtering is desirable.

## Common target protocols

Ranked by adoption in third-party inverters that home-storage installers use:

### Pylontech CAN (most widely supported)

A 500 kbps CAN bus protocol used by Pylontech US-series batteries. Supported natively by:

- **Victron** (Cerbo GX / Multi RS / etc.) - first-class support
- **Deye** (SUN-xK-SG family hybrids)
- **Goodwe** (ET / EH / EM hybrids)
- **Sungrow** (SH-RS / SH-RT residential)
- **Sofar Solar** (HYD-ES / ME-3000SP / etc.)
- **GroWatt** (SPH / MIN / MAX with battery)
- **SolaX** (X1 / X3 hybrids)
- Many cheaper Chinese hybrids advertising "Pylontech compatible"

The Pylontech CAN protocol uses 11-bit standard CAN IDs and broadcasts a fixed set of frames at ~1 Hz. The bridge typically sends:

| Purpose | Notes |
|---|---|
| Charge / discharge voltage and current limits | `BatteryChargeVoltage`, `MaxChargeCurrent`, `MaxDischargeCurrent`, `LowVoltageDisconnect` |
| State of Charge / State of Health | percentage |
| Pack voltage, current, temperature | absolute values |
| Battery type / manufacturer name | ASCII strings, often "PYLON" or similar |
| Protection / warning bit-fields | per-fault flags |

The exact CAN IDs and field layouts are documented in publicly available Pylontech protocol references (search "Pylontech BMS CAN protocol" - several copies of the original spec are mirrored online). Open-source bridges (e.g. various Victron / OpenInverter projects) are good reference implementations.

**Bridge unit conversion notes:**

- GivEnergy Block 2 capacity values are in 0.1 Ah units (e.g. `0x48A8` = 18600 = 186.00 Ah). Pylontech CAN expects Ah.
- GivEnergy reg 25 current limit is 0.01 A units (90.00 A). Pylontech CAN expects 0.1 A.
- Cell voltages from GivEnergy IR Block 3 are raw mV. Pylontech CAN min/max cell voltages are 1 mV units - direct passthrough.
- SoC from GivEnergy Block 2 byte 25 is direct % - direct passthrough.
- GivEnergy temperatures in IR Block 1 are 0.1 deg C units. Pylontech CAN uses 0.1 deg C - direct passthrough.

### Victron VE.Can / VE.Bus

Victron's native CAN protocols (separate from their Pylontech-compatible mode). Cleaner protocol but limited to Victron ecosystem.

If the target is a Victron Cerbo GX, **Pylontech CAN compatibility mode is the easier integration path** - the Cerbo treats Pylontech-compatible batteries as first-class citizens and exposes the standard "Battery monitor" service to the rest of the system.

### BYD CAN

Used by BYD's Premium HV / Premium LV battery line. Different field layout from Pylontech but conceptually similar (1 Hz broadcast frames). Several inverters support BYD-mode as an alternative to Pylontech-mode.

### Pylontech RS485 / Modbus

Some inverters speak to Pylontech batteries over RS485 instead of CAN. Less common than CAN but worth supporting if the bridge has an extra RS485 port available.

### SunSpec Modbus

Open standard maintained by SunSpec Alliance. Support is growing but not as widespread as Pylontech CAN. Worth offering as a future option.

## Hardware

A small Linux SBC or microcontroller with both RS485 and CAN interfaces:

| Approach | Notes |
|---|---|
| Raspberry Pi + USB-RS485 + USB-CAN | Easy to develop in Python; ~50 GBP total. CAN interfaces like the [Waveshare USB-CAN-A](https://www.waveshare.com/usb-can-a.htm) or USB-CAN modules with SocketCAN support work well. Linux SocketCAN integration is mature. |
| Raspberry Pi + RS485 HAT + CAN HAT | Cleaner physical packaging. Several HATs available. |
| ESP32 with built-in CAN + USB-RS485 | Cheaper, lower-power, fits in a small enclosure. Need to choose ESP32 module with TWAI (CAN) controller. |
| Industrial gateway (Moxa, Advantech, ...) | Overkill but reliable. |

For development, an SBC running Python or Go is easiest. For production deployment, ESP32 or similar is more cost-effective.

## Polling on the GivEnergy side

The bridge plays inverter role on the RS485 bus. It must:

- **Set itself as the bus controller** (only one controller at a time on RS485). If the real GivEnergy inverter is removed, the bridge takes its place. If it's still present, the bridge must NOT also be a controller - it would conflict.
- **Drive RS485 DE/RE correctly** before TX (most USB-RS485 dongles handle this automatically; check before deploying).
- **Send HR poll** to device 1 every ~245 ms with the canonical request bytes `01 03 00 00 00 1C` + CRC.
- **Send IR polls** rotating through devices 1..N at ~10 s spacing per query, three blocks each.
- **Parse FC=3 responses normally** (standard Modbus framing).
- **Parse FC=4 responses with the non-standard format** (device + FC + addr_echo + data + CRC, no byte_count). Length is implicit from the request's count.

The polling pattern documented in [06-wire-captures.md](06-wire-captures.md) is what the GivEnergy battery firmware expects to see; replicating it gives the BMS no reason to behave differently than it would with a real inverter.

## Latency considerations

| Side | Inherent rate |
|---|---|
| GivEnergy HR poll (current source data) | 4 Hz (245 ms cadence) |
| GivEnergy IR Block 1/2/3 (slower telemetry) | 0.1 Hz per block per device |
| Pylontech CAN broadcasts | 1 Hz typically |
| Other CAN protocols | 1 Hz typically |

The bridge has plenty of headroom. The real engineering challenge is on data freshness: cell voltages from GivEnergy update only when Block 3 is polled (about once every 10 s per device). For most inverter use cases that is fine. If a third-party inverter expects sub-second cell voltage updates (rare), the bridge should poll Block 3 more aggressively.

## Validation envelopes from the third-party side

Different inverter targets have their own validation rules. Common pitfalls:

- **Pylontech CAN protections**: most inverters check the `Pylontech_BatteryChargeVoltage` and `MaxChargeCurrent` for sanity. If they go to 0 unexpectedly, the inverter stops charging. The bridge should derive these from GivEnergy's reg 25 (current limit, 0.01 A units) and the BMS firmware version reported in HR reg 13 (which can be used to look up appropriate per-firmware defaults).

- **Manufacturer string expectation**: some inverters reject unrecognised manufacturer strings. Use a string the inverter is known to accept (e.g. "PYLON" or whatever its compatibility documentation lists).

- **Cell voltage range**: Pylontech CAN expects cells in mV in the 2000-4000 range. GivEnergy LFP cells stay well within this. No transformation needed beyond endianness handling (GivEnergy is big-endian on the wire; CAN frames typically little-endian).

- **No-data timeout**: most inverters mark the battery offline if no CAN frame arrives within a few seconds. Keep the bridge's CAN broadcast loop running even if the GivEnergy poll stalls briefly - send the last known good values with a stale-data flag if the protocol supports it.

## Recommended values from GivEnergy state

Mapping GivEnergy register fields to fields the bridge must produce:

### From HR(0..27)

| GivEnergy field | Bridge mapping |
|---|---|
| Reg 13 = firmware version (3022) | optional - some target protocols include a version field |
| Reg 17 / 18 = device ID (32-bit aggregate) | optional - serial identification |
| Reg 19 = 8-flag composite status | bit-by-bit map to target protocol's protection / warning flags (semantics still partly TBD - see [02-holding-registers.md](02-holding-registers.md)) |
| Reg 23 = signed pack current (0.01 A) | Pylontech CAN: `BatteryCurrent` in 0.1 A. Convert: divide by 10. |
| Reg 25 = current limit constant (90.00 A) | Pylontech CAN: `MaxChargeCurrent` and `MaxDischargeCurrent` (in 0.1 A). Convert: divide by 10. |

### From IR Block 1

| GivEnergy field | Bridge mapping |
|---|---|
| Bytes 12-21 = 5 temperatures (0.1 deg C) | aggregate: report min, max, average to the inverter; or expose per-sensor if the target protocol supports it |
| Bytes 22-23 = some flag | possibly maps to a "balancing active" or "charging accepted" flag |

### From IR Block 2

| GivEnergy field | Bridge mapping |
|---|---|
| Byte 0 = cell count | typically passthrough |
| Bytes 1-2 = cycle count | passthrough |
| Bytes 7-8 = pack voltage (0.001 V) | Pylontech CAN: `BatteryVoltage` in 0.01 V. Convert: divide by 10. |
| Bytes 15-16 = calibrated capacity (0.1 Ah) | Pylontech CAN: `RatedCapacity` in 0.1 Ah - direct passthrough. |
| Bytes 19-20 = design capacity (0.1 Ah) | optional - Pylontech CAN reports nominal capacity which is design capacity. |
| Bytes 23-24 = remaining capacity (0.1 Ah) | useful for SoC calculation: `SoC = remaining / calibrated x 100` |
| Byte 25 = SoC % | Pylontech CAN: `SoC` direct. |

### From IR Block 3

| GivEnergy field | Bridge mapping |
|---|---|
| Bytes 0-31 = 16 cell voltages (raw mV BE) | Pylontech CAN: `MinCellVoltage`, `MaxCellVoltage`, `MinCellId`, `MaxCellId`. Bridge computes min/max/index from the array. |
| Bytes 36-37 = max cell voltage | passthrough |
| Bytes 38-39 = min cell voltage | passthrough |

## Multi-battery handling

GivEnergy supports up to 5 paralleled batteries (devices 1..5). The bridge has two strategies:

- **Aggregate** all batteries into one virtual "stack" presented to the inverter. Sum currents and capacities; report worst-case cell voltages and temperatures; use the lowest SoC. This is the most compatible approach (third-party inverters expect a single battery interface).
- **Pass-through per-battery** if the target protocol supports multi-battery (e.g. some Pylontech CAN modes do). More accurate but rarely needed.

The aggregate approach is recommended for first implementations.

## Test methodology

Same general approach as the emulator (see [07-emulator-implications.md](07-emulator-implications.md)):

1. **Bench-test against the real GivEnergy battery** without an inverter. Verify the bridge can poll and parse correctly; validate the produced CAN frames against a CAN sniffer (e.g. `candump` in Linux SocketCAN, or [SavvyCAN](https://www.savvycan.com/) on a laptop with a USB-CAN dongle).
2. **Replay-test** by recording known-good Pylontech CAN traffic from a real Pylontech battery and running the bridge against canned GivEnergy data; verify the bridge produces equivalent frames.
3. **Side-by-side test** with a real Pylontech battery and the bridge-presented "virtual Pylontech" on the same inverter, comparing inverter behaviour.
4. **End-to-end** with the target inverter once bench tests pass.

## Common pitfalls

1. **CAN bit-rate / termination mismatch**. Pylontech CAN is 500 kbps. Target inverter must be configured for the same rate. Both ends of the CAN bus must have 120 ohm termination.

2. **Endian confusion**. GivEnergy Modbus is big-endian (network byte order); CAN protocols are typically little-endian. Easy to get backwards.

3. **Forgetting the FC=4 non-standard framing on the GivEnergy side**. If the bridge uses a stock Modbus library, FC=4 reads will fail with CRC errors. See [01-protocol.md](01-protocol.md) for the framing details. The library may need to be patched.

4. **Stale data on the inverter side**. If the GivEnergy poll stalls (e.g. battery briefly disconnected during commissioning), the bridge must keep broadcasting CAN frames with the last known values - some inverters mark the battery offline if frames stop arriving for a few seconds.

5. **Target inverter changes its expected manufacturer string between firmware versions**. Some inverters narrow what they accept over time. If the bridge stops working after an inverter firmware update, check the manufacturer-name field first.

6. **Multi-battery aggregation arithmetic errors**. Total pack current is sum across batteries; total voltage is one battery's voltage (they're paralleled, not series); SoC should be the minimum (since that's the limiting factor). Mistakes here cause weird charge/discharge behaviour.

## See also

- [01-protocol.md](01-protocol.md) - GivEnergy Modbus protocol fundamentals (the bridge's input side)
- [02-holding-registers.md](02-holding-registers.md), [03-input-registers.md](03-input-registers.md) - GivEnergy register layout (what to read)
- [05-inverter-firmware.md](05-inverter-firmware.md) - what a real GivEnergy inverter does on the bus (what the bridge mimics on the GivEnergy side)
- [07-emulator-implications.md](07-emulator-implications.md) - the dual problem (Goal 1 emulator)
