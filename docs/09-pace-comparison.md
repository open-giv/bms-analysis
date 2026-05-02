# GivEnergy Modbus vs PACE / Pylontech

The GivEnergy LV BMS speaks two protocols at the same time:

- **Modbus-RTU on USART3** (the inverter-facing link this documentation focuses on)
- **PACE / Pylontech v2.5 on UART4** (inter-pack comms when batteries are stacked)

Both protocols expose substantially the same BMS state, populated from the same internal data structures. This document maps the GivEnergy Modbus register layout against the equivalent PACE fields, so anyone familiar with one can quickly orient themselves in the other.

This matters for two reasons:

1. **An emulator (Goal 1) can be built around an existing PACE client library.** Read your underlying battery's state into a PACE-compatible internal model, then serialize that model twice - once as PACE bytes for any UART4 listener, once as the GivEnergy flat Modbus layout for the inverter.
2. **The bridge (Goal 2) is trivially close to a Pylontech CAN bridge.** Pylontech CAN is itself a wire format for the same PACE data model. Most fields map directly with at most a unit-scale change.

## Field map

| GivEnergy Modbus location | Field | PACE equivalent | Same encoding? |
|---|---|---|---|
| IR Block 3 bytes 0-31 | 16 cell voltages | `CELL_VOLT[]` in `CID2=0x42` INFO | Yes - same raw mV big-endian, no offset |
| IR Block 1 bytes 22-31 | 5 temperatures | `TEMP[]` in `CID2=0x42` INFO | Same source data, different on-wire. PACE sends `(decidegC + 2730)` (i.e. `0.1 K`); Modbus subtracts the same bias before TX. The `+2730` internal storage IS the PACE encoding. |
| IR Block 2 byte 0 | Cell count | `CELL_NUM` | Yes |
| IR Block 2 bytes 1-2 | Cycle count | `CYCLE_COUNT` | Yes |
| IR Block 2 bytes 7-8 | Pack voltage (mV) | `PACK_VOLTAGE` (mV) | Yes |
| IR Block 2 bytes 15-16 | Calibrated capacity (0.1 Ah) | `TOTAL_CAP` (`USERDEF`-controlled unit) | Yes |
| IR Block 2 bytes 23-24 | Remaining capacity (0.1 Ah) | `REMAIN_CAP` | Yes |
| HR reg 23 (bytes 46-47) | Pack current (signed deciAmps) | `PACK_CURRENT` (signed 0.1 A) | Yes - same unit |
| IR Block 3 bytes 36-37 | Max cell voltage (mV) | Not in standard PACE Get Analog - clients compute | GivEnergy explicit |
| IR Block 3 bytes 38-39 | Min cell voltage (mV) | Not in standard PACE Get Analog - clients compute | GivEnergy explicit |
| IR Block 2 byte 25 | SoC % (direct) | Not in standard PACE Get Analog - clients compute from REMAIN/TOTAL | GivEnergy explicit |
| IR Block 2 bytes 19-20 | Design capacity (0.1 Ah) | Not in standard PACE - vendor extension territory | GivEnergy explicit |
| IR Block 1 bytes 0-19 | Serial number (20-char ASCII) | `CID2=0x46` "Get Manufacturer Info" | Same data, separate PACE command |
| IR Block 2 bytes 35-36 | BMS firmware version (e.g. `0x0BCE`) | Part of `CID2=0x46` Manufacturer Info | Same data, packed differently |
| HR reg 19 byte | 8-flag composite status | `CID2=0x44` "Get Alarm Data" (per-cell + pack-level) | Same concept, collapsed into one HR byte vs PACE's separate alarm-data response with per-cell granularity |
| HR reg 25 (bytes 50-51) | Configurable current limit (`*(u16)src * 100`) | `CID2=0x47` "Charge/Discharge Mgmt Info" | Same data, separate PACE command |

## Architecture: same data, two envelopes

The BMS firmware maintains the underlying pack state in two parallel SRAM structures:

| Structure base | Size | Used by |
|---|---|---|
| `0x2000_3D6A` | 145 bytes / pack, up to 6 packs | Modbus FC=4 per-pack response |
| `0x2000_14EA` | 80 bytes / slice, up to 6 slices | PACE inter-pack response (CID2=0x42) |

Both are populated from the same upstream sources (cell-monitor reads, current-sensor ADC, etc.). The two response builders read from their respective structures and emit the appropriate wire format.

This is consistent with the OEM lineage: a Pylontech-pattern BMS firmware base, with GivEnergy specifying a custom Modbus interface bolted on top of the same internal state.

## What's distinctively GivEnergy

Not the field semantics - those are essentially PACE's. The bespoke parts are about *packaging*:

1. **Flat Modbus register space** instead of variable-length PACE INFO frames. ~60 registers across HR + three IR blocks.
2. **Explicit precomputed fields** (SoC %, min/max cell V, design capacity) that PACE clients would derive from primitives.
3. **Status/alarm flags collapsed into one HR byte** (reg 19, 8-bit OR composite) instead of PACE's separate `CID2=0x44` "Get Alarm Data" with per-cell granularity.
4. **The non-standard FC=4 response framing** (echoed start address in place of byte_count) - this is a peculiarly GivEnergy thing with no PACE parallel.
5. **Multiple smaller IR blocks** (instead of one big response) split as 21+19+20 registers - probably to fit each block under any client-side `byte_count`-derived limits and to allow incremental polling.

## Implications for an emulator (Goal 1)

You can structure the emulator's internal model around PACE-compatible primitives:

```
struct pack_state {
    uint8_t  cell_count;         // typically 16
    uint16_t cell_mV[16];        // raw, big-endian on the wire
    uint8_t  temp_count;         // typically 5
    int16_t  temp_decidegC[5];   // signed, raw on the wire (subw bias is firmware-internal)
    int16_t  pack_current_dA;    // signed deciAmps
    uint16_t pack_voltage_mV;
    uint16_t total_cap_cAh;
    uint16_t remain_cap_cAh;
    uint8_t  soc_pct;
    uint16_t design_cap_cAh;   // GivEnergy explicit
    uint16_t cycle_count;
    char     serial[20];
    uint16_t firmware_version;   // e.g. 0x0BCE
    uint8_t  status_flags;       // 8-bit composite
    uint16_t current_limit_dA;   // = config_value * 100
};
```

Two serializers consume this:

- **Modbus serializer** packs into HR + IR Block 1/2/3 with the GivEnergy-specific framing (esp. the FC=4 addr_echo) per [01-protocol.md](01-protocol.md).
- **PACE serializer** (optional - only if you want to also satisfy any UART4 listener) packs into the standard PACE `CID2=0x42` / `CID2=0x44` / `CID2=0x46` responses.

If you only need to satisfy a GivEnergy inverter, you only need the Modbus serializer. The PACE side can be a stub.

## Implications for a bridge (Goal 2)

Pylontech CAN broadcasts the same fields the table above lists, just packaged as 8-byte CAN frames at fixed message IDs. The bridge logic becomes:

```
poll GivEnergy battery (Modbus master)
  -> parse HR + IR responses into pack_state
  -> [optionally apply unit conversions]
  -> serialize as Pylontech CAN frames at 1 Hz
```

Most field-by-field translations are direct:

| pack_state field | Pylontech CAN |
|---|---|
| `pack_voltage_mV` | divide by 10 -> `BatteryVoltage` (0.01 V) |
| `pack_current_dA` | already 0.1 A -> `BatteryCurrent` direct |
| `soc_pct` | direct |
| `total_cap_cAh` | divide by 10 -> `RatedCapacity` (0.1 Ah) |
| `cell_mV[]` | compute min/max -> `MinCellVoltage`, `MaxCellVoltage` (1 mV) |
| `temp_decidegC[]` | compute min/max -> `MinCellTemperature`, `MaxCellTemperature` (0.1 deg C) |
| `current_limit_dA` | -> `MaxChargeCurrent` and `MaxDischargeCurrent` (0.1 A) |
| `status_flags` | bit-by-bit map to Pylontech protection / warning flags |

See [08-bridge-implementation.md](08-bridge-implementation.md) for the bridge's full architecture; this comparison just highlights why the translation is unusually clean - both are wire formats for the same PACE data model.

## See also

- [01-protocol.md](01-protocol.md) - GivEnergy Modbus framing details
- [02-holding-registers.md](02-holding-registers.md) - HR field-by-field
- [03-input-registers.md](03-input-registers.md) - IR field-by-field with encoding details
- [04-bms-firmware.md](04-bms-firmware.md) - BMS firmware analysis (incl. the inter-pack PACE channel and FC=4 internal encoding)
- [07-emulator-implications.md](07-emulator-implications.md) - Goal 1 implementation
- [08-bridge-implementation.md](08-bridge-implementation.md) - Goal 2 implementation
