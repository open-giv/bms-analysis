# BMS firmware static analysis

The official GivEnergy BMS firmware can be statically analysed to reveal the protocol implementation in detail. This complements the empirical wire captures by showing exactly how the BMS responds to queries, what data structures back the registers, and which validation rules apply.

## Firmware format

GivEnergy distributes the BMS firmware as `BMS_ARM.bin` files (one per firmware version). The format:

- 4-byte vendor header: `0xVVVV 0x5566` (version little-endian + magic). E.g. 3022 starts `CE 0B 66 55`.
- Standard Cortex-M vector table at file offset 4.
- **Load address is `0x08010000`** (NOT `0x08000000`). The first 64 KB of flash (`0x08000000`-`0x0800FFFF`) is reserved for the bootloader. Confirmed by reset-vector arithmetic: reset handler is at `0x08027C48` in 3022, which is inside the firmware range only if load is `0x08010000`. With load `0x08000000`, the reset vector would point outside the firmware.
- Plain Thumb-2 code, no compression or encryption.

Versions analysed:

| Version | Size | Function count | Load address | Notes |
|---:|---:|---:|---|---|
| 3017 | 143,370 B | 443 | `0x08010000` | Older code structure; major refactor before 3020 |
| 3020 | 118,794 B | 464 | `0x08010000` | Wire-protocol-identical to 3022 |
| 3022 | 119,818 B | 492 | `0x08010000` | Reference version for analysis below |
| Gen 3 / 4xxx | ~165 KB | -- | (different) | Different protocol architecture, not analysed here |

3020 and 3022 produce byte-identical wire behaviour - addresses shift but field meanings don't change. 3017 has the same fields but different code structure.

### Cross-version stability (audit, 2026-05)

A focused cross-version diff of 3017/3020/3022 found the following stable across all three:

- **Load address (`0x08010000`)** and 4-byte header pattern.
- **Calibration polynomial constants** used by `compute_filtered_current` (the function that derives HR23 from the ADC ring buffer): `0.930`, `0.961`, `0.962` are byte-identical in the literal pools of all three versions. The current calibration is firmware-version-stable, not per-hardware-revision.
- **Wire protocol layout** for HR(0..27) and IR blocks 1-3.

What changed between versions:

- **3017 -> 3020**: major code refactor. 24 KB of code removed, function count grew (443 -> 464). Same functionality, decomposed into smaller, more focused functions.
- **3020 -> 3022**: incremental (+1 KB, +28 functions). The cross-charge controller `compute_pack_current_limits` appears to be a 3022-era addition: the FC4 pack table base `0x20003D6A` is referenced 8 times in 3022 and zero times in 3017/3020. HR26/27 source bytes (`0x20000142` / `0x20000144`) existed in earlier versions but with different reference patterns — the current iterating-over-6-pack-slots controller pattern is new in 3022.

**Implications for emulators**: target 3022 wire behaviour as default. For older-firmware inverters, the HR26/27 fields may behave differently or be statically zero.

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

## Runtime mirror tasks (dynamic-trace findings)

The HR table's volatile fields (HR11, HR20 D8/D9, HR21) are not written directly by visible code paths. They're populated each tick by a chain of three "hidden" functions that Ghidra's autoanalysis missed entirely -- no auto-created function entries, no resolvable callers. They were located by **dynamic execution under Unicorn**: hook `UC_HOOK_MEM_WRITE` on the watched SRAM range, call candidate function entries, and observe which one fires the hook.

### FUN_0802224C - the giant pack-walking scheduler tick

Flash entry `0x0802224C` (no Ghidra auto-fn). 10-register `push.w` prologue. The function walks the 6 FC=4 pack slots and aggregates state into the HR-source struct:

| PC | Writes | Semantic |
|---|---|---|
| `0x08022284` | `*(float*)0x20000180 = uint_to_float(*(u16*)0x20000186)` | HR11 float mirror (= `uint_to_float(remaining_cAh)`) |
| `0x080224A0` | (same site in an alternate path) | HR11 float mirror |
| `0x080224A6` | `*(u16*)0x20000184 = *(u8*)0x20000189` (zero-extended) | HR21 u16 mirror (= SoC %) |
| `0x0802257A` | `*(u16*)0x20000184 = new_value` only if `|new - current| <= 1` | HR21 delta-limiter |
| `0x08022618` | `*(u16*)0x20000184 = 100` | HR21 clamp-to-100 (calibration path) |
| `0x08022442` | `*(u8*)0x200000D8 = aggregate(pack[N*145 + 0x8D])` | HR20 bit 3 (Under-Voltage) global aggregate |
| `0x08022456` | `*(u8*)0x200000D9 = aggregate(pack[N*145 + 0x8D])` | HR20 bit 2 (Over-Voltage) global aggregate |

The HR21 delta-limiter explains the slow SoC drift Ken observed across the capture window: even when the upstream SoC byte at `0x20000189` jumps, the mirror only advances `±1` per tick.

### FUN_080181F2 - the SoC computer

Flash entry `0x080181F2` (no Ghidra auto-fn). 8-register `push.w` prologue. Gated by a `0x1A5E00` (~1.7M) tick counter -- the SoC value is only recomputed periodically. When the gate fires:

```c
if (counter++ >= 0x1A5E00) {
    counter = 0;
    float design = *(float*)0x20000168;     // EEPROM-persisted design capacity
    float remain = *(float*)0x2000017C;     // EEPROM-persisted remaining capacity
    if (remain > design) {                  // "calibration trigger" condition
        *(u8*)0x20000188 = 100;             // max-SoC tracker
        if (some_flag == 1 || current_SoC == 100) {
            *(u8*)0x20000189 = 100;         // clamp
        } else {
            // soc = (remain / design) * 100.0 + 0.5  (round)
            *(u8*)0x20000189 = (u8)(remain / design * 100.0f);
        }
    }
}
```

Float literals embedded in the function: `0x3FE00000` = `0.5f` (rounding), `0x42C80000` = `100.0f` (percent scale). Both source floats are loaded from EEPROM at boot by `FUN_080134B0` (the BMS state struct loader spanning SRAM `0x20000160..0x20000196`).

### Parent scheduler X (no Ghidra auto-fn) at flash `0x08011876`

Calls `FUN_0801151E` (protection-bit setter) and `FUN_0802224C` (HR mirror task) in a loop. No `push` prologue (uses no callee-saved registers), no `BL`/`B`/`BX` caller anywhere in firmware, and its address does NOT appear as a 4-byte data literal. Conclusion: invoked via a **SRAM-resident function-pointer table populated at boot**, which is built dynamically and invisible to static analysis.

### Source-byte chain summary

```
[opaque coulomb counter]
    writes *(u16*)0x20000186 (remaining cAh) and *(float*)0x2000017C (calibrated remaining)
        |
        v
FUN_080181F2 reads 0x2000017C / 0x20000168 -> writes *(u8*)0x20000189   (canonical SoC byte)
        |
        v
FUN_0802224C reads 0x20000186 -> writes *(float*)0x20000180 (HR11 mirror)
              reads 0x20000189 -> writes *(u16*)0x20000184 (HR21 mirror, delta-limited)
              walks packs[0..5][0x8D] -> writes *(u8*)0x200000D8 / D9 (HR20 OV/UV aggregates)
        |
        v
fc3_update_task reads HR11 mirror -> writes whole-Ah into HR table @ HR[11]
                  reads HR21 mirror -> writes u16 into HR table @ HR[21]
                  reads D8 / D9 -> contributes to HR table @ HR[20]
```

For an emulator that runs the firmware in Unicorn as a backend: write directly to `*(u16*)0x20000186` (cAh) and `*(u8*)0x20000189` (SoC) and the mirror cascade will populate the HR table each tick. The opaque upstream coulomb counter is not needed.

### Alarm source architecture

The three packed alarm bitmaps that feed HR19 bits 7,8 and HR20 bits 1,2,5-8 share a common architecture:

| Byte | Semantic | Setters | Clearer state machine |
|---|---|---|---|
| `0x20000279` | Current alarm bitmap (over-current, short-current) | `pace_cid2_dispatch` (mostly) + scattered per-protection setters | `FUN_0801ED08` |
| `0x2000027A` | Temperature alarm bitmap (4 bits: charge/discharge over/under-temp) | `pace_cid2_dispatch` + per-protection setters | `FUN_0801F02C` |
| `0x2000027B` | Voltage alarm bitmap | `pace_cid2_dispatch` (primary) + `FUN_0801F3F4` | `FUN_0801F3F4` |

All three clearer functions share the same structure: iterate 8 bits, check per-bit recovery thresholds for 3+ cycles, then clear via `*byte = *byte & ~mask`. The **set** side is driven primarily by incoming PACE frames from the AFE chip -- the BMS receives alarm bits from the AFE and forwards them; it does not generate them locally.

This three-byte split (current / temperature / voltage) lines up exactly with the three alarm-category clusters in GivTCP's `battery_fault_code` enum.

The "any protection active" byte at `*(u8*)0x2000009D` (HR19 bit 4 source) is a separate aggregate: setter analysis shows **only bit 1** is ever set or cleared in this byte. Effectively a single boolean, not an 8-bit bitmap. Setters: `FUN_0801151E` (counter timeout `>= 100`) and `FUN_0801ED08` (AFE-flag-set path); both `ORR #0x02`. The 8-bit iteration inside `FUN_0801ED08` is over a DIFFERENT upstream event bitmap; the aggregated result lands in bit 1 of `0x2000009D`.

### Limits - what static + dynamic both miss

Several writers remain opaque even after dynamic Unicorn tracing of all candidate function entries (492 Ghidra functions + 446 push-prologue addresses + 14 IRQ vector handlers):

- **HR11 cAh u16 writer** (`*(u16*)0x20000186`): hypothesis is a pack-online state-change handler that fires only when a new pack appears on the PACE bus. Would require sustained simulation with synthesized AFE traffic.
- **HR15 bits 1/2 sources** (`*(u8*)0x20000197` / `0x20000198`): written inside `pace_cid2_dispatch` via base+offset addressing, only when a valid PACE frame is in the RX buffer.
- **HR16 source** (`*(u8*)0x20000518`): same situation -- PACE-derived, requires frame injection.
- **Block 3 mystery field sources** (`pack[N*145 + 0x73..0x76]`): same -- written by deep PACE chain.

All four share one root cause: they're populated by PACE-protocol code paths that fire only on bus events. For an emulator that synthesizes wire output directly, none matter; for a deeper firmware-internals model, frame injection or long-running simulation would be needed.

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
