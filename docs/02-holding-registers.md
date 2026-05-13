# Holding registers (FC=3)

The inverter polls a single block of 28 holding registers (offsets `0x0000`-`0x001B`) from **device 1 only**, every ~245 ms. This is the highest-rate query on the bus and carries the BMS's real-time status.

## Poll request and response shape

```
Request:   01 03 00 00 00 1C 44 03                     (8 bytes; device=1, FC=3, start=0, count=28, CRC)
Response:  01 03 38 [56 data bytes] [crc_lo crc_hi]    (61 bytes; standard Modbus with byte_count=0x38)
```

FC=3 uses standard Modbus framing (with byte_count, unlike FC=4). See [01-protocol.md](01-protocol.md) for details.

## Cadence

| Metric | Value |
|---|---|
| Average gap between requests | 245 ms |
| Minimum gap | 231 ms |
| Maximum gap | 481 ms |
| BMS turnaround latency (req -> rsp) | ~101 ms (p95: 103 ms; range 90-114 ms) |
| Device addressed | 1 only (the primary battery; HR is never polled to other devices) |

The device-1 hard-coding is confirmed both empirically and from inverter firmware analysis: the FA-series Gen 3 builder writes `movs r0, #1; strb r0, [sp]` for the device byte unconditionally on the HR path.

## Register layout

The 28 registers (= 56 bytes) decoded at the byte level:

| Reg | Bytes | Empirical observation | Firmware-derived interpretation |
|----:|---|---|---|
| 0   | 0-1   | constant `0x0065` (101) | Init writes literal `0x65`. **Fixed protocol/device marker constant** (not the device address - that's set by dipswitches). |
| 1-4 | 2-9   | constant `0xFFFF` x 4 | Never written after the 0xFFFF init. **Truly unused / reserved.** |
| 5-9 | 10-19 | ASCII serial number (e.g. `XXXXXXXXXX`) | 5 halfwords copied big-endian from a 10-byte SRAM struct. |
| 10  | 20-21 | constant `0xFFFF` | Never written. **Unused.** |
| 11  | 22-23 | (`0x00BA` and `0x0174`) Total Ah of batteries online.  Two values seen are multiples of documented battery Ah capacity.  186 seen when one battery at min charge during calibration cycle, else 372. | **Remaining Ah of batteries online**, in whole amp-hours. Computation: `int(*(float*)0x20000180 / 100.0)` where the source float is `uint_to_float(remaining_cAh)` (a centi-Ah accumulator). One ~9.5 kWh / 51.2 V LFP pack ≈ 186 Ah; two = 372 Ah. |
| 12  | 24-25 | constant `0x0030` (48) | Init writes literal `0x30`. Possibly a **hardware revision** field. |
| 13  | 26-27 | constant `0x0BCE` (3022) | Confirmed: `movw r0, #0xbce; strh r0, [r4, #0x1a]`. **BMS firmware version.** |
| 14  | 28-29 | constant `0x0000` in capture | **Inverter-set "charge mode selector active" flag.** Source `*(u8*)0x200000E9` is a normalised mirror of `*(u8*)0x200000E8`. The setter is the Modbus FC=6 reg-2 path (TBB at flash `0x0801E240` entry 2) where the inverter writes a charge-mode index `0..9` (`0` = auto, `1..9` = forced mode). `fc3_update_task` normalises non-zero values to `1`. HR14 is therefore `1` iff the inverter currently has a non-zero charge mode selected. Stays at `0` in normal auto-mode operation, which matches the capture. |
| 15  | 30-31 | constant `0x0000` in capture | 3-bit OR-mask. **Sources confirmed empirically**: bit 0 (lsb) from `*(u8*)0x2000013F` (non-zero); bit 1 from `*(u8*)0x20000198`; bit 2 from `*(u8*)0x20000197`. **Bit 0 fully writer-traced**: `compute_pack_current_limits` reads HR21's SoC source `*(u16*)0x20000184`, compares to `0x64`, writes `1` to `*(u8*)0x2000013F` if SoC >= 100 else `0`. So **bit 0 = "primary pack SoC = 100%"** ("balancing-ready"). Bits 1 and 2 are PACE-derived (written by `pace_cid2_dispatch` via base+offset addressing only when a valid PACE frame is in the RX buffer; specific PACE field for each bit not yet traced). See [Register 15 Bits](#register-15-bits). |
| 16  | 32-33 | constant `0x0000` in capture | **AFE-derived state byte** at `*(u8*)0x20000518`. The address sits inside a struct accessed via base+offset by `pace_cid2_dispatch` at flash `0x0801A17A` (nearby literals `0x20000524`/`0x20000530` are loaded inside that function). Ghidra's autoanalysis doesn't unify the indirect writes, but architecturally HR16 mirrors whatever state byte the PACE/Pylontech inter-pack protocol parser writes into that struct position. Specific PACE field unconfirmed -- would require frame-injection tracing. |
| 17  | 34-35 | varies (`0x114A`-`0x1219`, ~142 distinct values) | **Low 16 bits of a BCD-serial-derived hash.** Algorithm: 6 BCD bytes at SRAM `0x20000105..A` are reverse-byte-order copied to `0x20000190..A` by an upstream copier at flash `0x08001362`. Hash applies forward over `0x20000190..A`: `acc = (bcd_to_dec(b[i]) + acc) << shifts[i]` for `i=0..4` with `shifts=[4,5,5,6,6]`; then `r = bcd_to_dec(b[5]) + acc`. HR17 = `r & 0xFFFF`. **Empirically verified end-to-end.** |
| 18  | 36-37 | constant `0x389D` (14493) across all 829 captures | High 16 bits of the same hash: `(r >> 16) & 0xFFFF`. Constant per device because the 6 source bytes are the device serial fragment, fixed at manufacture. HR17/HR18 are effectively a per-device fingerprint, NOT a runtime state hash. |
| 19  | 38-39 | See [Register 19 Bits](#register-19-bits) | 8-bit composite status. All 8 bits empirically mapped to source addresses; see table. Bits 0/1 encode current direction via IEEE-754 equality with zero (not a sign-bit test). |
| 20  | 40-41 | See [Register 20 Bits](#register-20-bits) | 8-bit composite alarms / global alarm aggregate. All 8 bits empirically mapped to source addresses; see table. **D8/D9 writers fully traced via dynamic Unicorn**: `FUN_0802224C` (the giant pack-walking scheduler-tick that also writes HR11/HR21 mirrors) writes `0x200000D9` (over-V) at PC `0x08022456` and `0x200000D8` (under-V) at PC `0x08022442`. Bit-1-of-`0x20000279`/`27A`/`27B` (current/temp/voltage) alarm bitmaps are SET by `pace_cid2_dispatch` (driven by AFE alarms over the PACE protocol) and CLEARED by local state-machine recovery (`FUN_0801ED08`/`F02C`/`F3F4`). So HR20 bits are global OR-aggregates over all packs, not per-pack flags. |
| 21  | 42-43 | Battery state of charge 0-100 (%) | Direct copy of `*(u16)0x20000184`. **Confirmed SoC %** by dynamic Unicorn trace. The full chain: the SoC computer `FUN_080181F2` (no Ghidra auto-fn) runs `*(u8*)0x20000189 = (u8)( *(float*)0x2000017C / *(float*)0x20000168 * 100.0f )` gated by a ~1.7M-tick counter; the HR-mirror task `FUN_0802224C` then runs `HR21 = *(u8*)0x20000189` (zero-extended to u16). A second HR21 write site in `FUN_0802224C` at `0x0802257A` is a delta-limiter (only updates if the new value is within +/-1 of current), and a third at `0x08022618` is a "clamp to 100%" path. The delta-limiter explains why Ken's capture saw SoC drifting slowly. The upstream u16 cAh counter at `0x20000186` (which HR11's float mirrors) is **also now writer-traced** via Cortex-M boot-from-reset Unicorn harness: it's written by the PACE CID2 = 0xA7 handler at flash PCs `0x0801AB7C` (high byte) / `0x0801AB90` (low byte), reading 4 ASCII hex chars from INFO offsets 0x15-0x18 and decoding via `hex_pair_to_byte` (flash `0x08010FD6`). So CID2 = 0xA7 is the AFE -> BMS command for "set remaining cAh". For emulator purposes, writing directly to `0x20000186` and `0x20000189` is sufficient since the mirror task copies them to HR11/HR21 each tick. |
| 22  | 44-45 | Battery voltage in units of **0.01 V** (centivolts), measured at primary battery pack. | `*(u16)0x20000114 / 10`. Source at `0x20000114` is in **mV** (a 48 V pack stores as 48000 = 0xBB80). Integer division by 10 produces 0.01 V resolution (4800 = 48.00 V). |
| 23  | 46-47 | Signed pack current of primary pack only, **units 0.01 A (centi-amps)**. Positive = charge, negative = discharge. Multi-pack inverters scale by pack count. | Computation: `(s16)f2iz(fdiv(*(float*)0x2000014C, 10.0f))` via `__aeabi_fdiv` (helper `0x0802C2A8` -- provably fdiv: body has `sub.w r2, r2, r3` exponent-subtraction and PC-relative reciprocal lookup table at `addw ip, pc, #0x108`) then `__aeabi_f2iz` (helper `0x0802C42C`). The source float at `0x2000014C` is in **milli-amps (mA)** -- the `/10` is a precision truncation, not a unit-step conversion. Wire value 1490 maps to float 14900 mA = 14.9 A. Confirmed empirically by wire trace: pack drawing ~14.9 A produces HR23 = 1490 in 0.01 A units. Earlier docs revisions oscillated between 0.1 A and 0.01 A; centi-amps is the settled answer. |
| 24  | 48-49 | **Maximum** cell temperature in whole °C. | 4-element MAX loop over `*(u16*)0x200011B6..BA` (4 temperature samples, raw format `°C × 10 + 2730`). Computation: `(max_raw - 2730) / 10`. So `0x0019` (25) -> 25 °C max cell temperature. Earlier readings called this "min cell voltage" -- that was wrong on two counts: the loop finds max (not min) and the source array is temperatures (not voltages). |
| 25  | 50-51 | constant `0x2328` (9000) in capture; **dynamic** when configured limit changes | `*(u16)(0x2000153A+8) × 100`. The `× 100` here is centi-amp scaling (0.01 A). Source at `0x2000153A+8` is **PACE slice 1, byte offset 8** (`g_pace_slice_table` base `0x200014EA` + slice 1 at `+0x50` + 8) -- a configured per-pack max-charge-current value, in whole amps (90 -> 9000). |
| 26  | 52-53 | Charge limit in 0.01A, honoured by Giv inverter | **Cross-charge current target -- charge side** of the pack-pair balancing controller. `*(u16)0x20000142`, written by `compute_pack_current_limits` (flash `0x080167BA`) which iterates the 6 FC4 pack slots, computes per-pack min/max budgets, and runs a ramp-with-hysteresis controller (1 A/call ramp step, 30% of configured max as cap). Tracks HR27 in steady state by conservation (`charge_current = discharge_current` at the coupling point); diverges during transitions because each side has independent ramp + converge logic. |
| 27  | 54-55 | Discharge limit in 0.01A, honoured by Giv inverter | **Cross-charge current target -- discharge side**. `*(u16)0x20000144`, same writer function as HR26, mirrored logic. |

### Register 15 Bits

3-bit OR-mask derived from three independent SRAM bytes. All bits empirically confirmed by toggling each source byte in firmware-execution and observing the resulting HR15 value.

| Bit | Source | Test | Notes |
|-----|--------|------|-------|
| 0 (lsb) | `*(u8*)0x2000013F` | `!= 0` | First flag; semantics unknown (likely a coarse pack-ready / discovery flag) |
| 1 | `*(u8*)0x20000198` | `!= 0` | Second flag; adjacent to the BCD-serial buffer at `0x20000190..A`, possibly serial-validity or PACE-AFE link state |
| 2 | `*(u8*)0x20000197` | `!= 0` | Third flag; adjacent to bit 1's source - likely related, same context |
| 3-15 | (none) | -- | Always zero; not written by any HR-update code path. |

The mask is built by `if (src != 0) hr15 |= (1 << bit)` for each source; HR15 is zero in Ken's capture because all three sources were zero throughout.

### Register 19 Bits

Register 19 is a set of status bits indicating the BMS state to the inverter. The behaviour-observation table below was derived from Ken's wire captures correlated to SoC / cycle conditions; the source-mapping table that follows comes from black-box execution of the firmware in Unicorn (vary one input, observe HR19).

**Behaviour observations (Ken):**

| Bit | Meaning (if set) | Analysis |
|-----|------------------|----------|
| 1 (lsb) | Discharging | From protocol analysis and from sign of `*(int*)0x2000014C` |
| 2 ||Normally high|
| 3 |Request Charge?|Normally high, low for extended period of min SOC|
| 4 |Battery MOSFETs enabled?|Normally high, oscillates below 4% SOC and near 100% SOC during calibration|
| 5 ||Normally low|
| 6 |Forbid Charge?|Normally low, high briefly at max SOC during calibration|
| 7 |Allow Discharge?|Normally high, low at minimum SOC during calibration|
| 8 |Allow Charge and Discharge?|Appears related to bits 6 & 7 - normally high, low when bit 7 low or bit 6 high|
| 9 || Unused? |
| 10 || Unused? |
| 11 || Unused? |
| 12 || Unused? |
| 13 || Unused? |
| 14 || Unused? |
| 15 || Unused? |
| 16 (msb) || Unused? |

**Firmware source mapping (empirical, Unicorn-verified):**

Bits below are **0-indexed** (matches the firmware shift amounts). Bit N in this table = bit N+1 in the 1-indexed table above.

| Bit (0-idx) | Source | Set when | Notes |
|----:|--------|----------|-------|
| 0 | `*(float*)0x2000014C` | float == 0 (idle) **OR** float < 0 (discharge) | **Current-direction encoding via IEEE-754 equality with zero, not the sign bit**. Idle = `01`, Charging = `10`, Discharging = `11`. Matches Ken's "Discharging" label - bit is set whenever current is not actively charging. |
| 1 | `*(float*)0x2000014C` | float != 0 (charging or discharging) | Together with bit 0, forms the 2-bit direction code. |
| 2 | `*(u8*)0x20000140` | byte == 0 | Logical NOT of the source byte. |
| 3 | `*(u8*)0x200000CE` | byte == 0 | Logical NOT of the source byte. |
| 4 | `*(u8*)0x2000009D` | byte != 0 | Direct truthiness test. |
| 5 | `*(u8*)0x20000141`, `*(u8*)0x20000140` | `[0x141] != 0 && [0x140] == 0` | Compound condition, gated by the same byte as bit 2. |
| 6 | `*(u8*)0x2000027B` | source bit 2 set | **Correction to earlier audit which said bit 5**. Empirical bit map: HR19 bit 6 (1-indexed bit 7) <- source bit 2. |
| 7 | `*(u8*)0x2000027B` | source bit 1 set | **Correction to earlier audit which said bit 6**. HR19 bit 7 (1-indexed bit 8) <- source bit 1. |

`*(u8*)0x2000027B` is a packed status byte populated by an upstream task; only bits 1 and 2 of it reach HR19. The remaining bits of `0x2000027B` flow into HR20 (see below).

#### Writer-traced semantics

Each of HR19's source bytes was traced back to the firmware function that writes it. The writer's logic determines the bit's actual meaning:

| Bit (1-idx) | Source byte | Writer function (flash addr) | Semantic |
|---:|---|---|---|
| 3 (charge req?) | `*(u8*)0x20000140` | `compute_pack_current_limits` @ `0x080167BA` | **Charge-vote consensus across packs.** Writer walks all 6 FC=4 pack slots; for each, reads `pack[0x8E]` (per-pack state byte). Increments a counter on state==1, resets it on state==2; final byte is `1` iff counter > 0. HR19 bit 3 is set when the source byte is 0 - i.e. "no charge-vote disagreement". |
| 4 (MOSFETs enabled?) | `*(u8*)0x200000CE` | `FUN_080209B0` (per-cell V checker) | **All 16 cells within voltage limits.** Writer scans the per-cell voltage array; flags a cell if `cell_mV < 2600` OR `cell_mV < (threshold + 50)` (when |current| < 1 A) OR `cell_mV < (threshold + 200)` (when |current| >= 1 A). Sets source byte to 1 if any cell fails, 0 if all pass. HR19 bit 4 is set when source byte is 0 -> "all cells OK". Confirms Ken's "Battery MOSFETs enabled?" observation. |
| 5 (normally low) | `*(u8*)0x2000009D` | `FUN_0801ED08` (clearer) + `FUN_0801151E` (setter) | **Any BMS protection flag active.** Setter-context analysis shows **only bit 1** of `0x2000009D` is ever set or cleared -- effectively a single boolean. Setters: `FUN_0801151E` (counter timeout `>= 100`) + `FUN_0801ED08` (AFE-flag-set path); both `ORR #0x02`. Clearer: `FUN_0801ED08` after recovery debouncing. The 8-bit iteration in `FUN_0801ED08` is over a DIFFERENT upstream event bitmap; the aggregated result lands in bit 1 of `0x2000009D`. HR19 bit 5 is set when source byte is non-zero -> "any protection active". Matches Ken's "normally low" observation. |
| 6 (forbid charge?) | `*(u8*)0x20000141` | `compute_pack_current_limits` | **Discharge-vote consensus across packs.** Same logic as bit 3 with reversed polarity: state==2 increments, state==1 resets. Combined with bit 3, the pair encodes a 2-bit consensus mode (idle/charge/discharge/disagreement). |

Bits 1/2 (charge/discharge direction encoding via IEEE-754 equality with zero on the float at `0x2000014C`) and bits 7/8 (sourced from `*(u8*)0x2000027B`) are populated by `pace_cid2_dispatch` from incoming PACE frames. **PACE injection in Unicorn confirms** two specific CID2 commands that set these bits:

| CID2 | Sets in `0x2000027B` | -> HR19 bit (1-idx) | Ken's label |
|---|---|---|---|
| `0x9A` | bit 1 (val `0x02`) | bit 8 | "Allow Charge and Discharge?" |
| `0x9B` | bit 2 (val `0x04`) | bit 7 | "Allow Discharge?" |

So **CID2 = 0x9A is the AFE "Allow Charge and Discharge" state-set command**, and **CID2 = 0x9B is the AFE "Allow Discharge" state-set command**. Both commands first clear the current-alarm and temperature-alarm bitmaps (a state-transition reset), then OR their specific bit into the voltage-alarm byte. Bits 1/2 of HR19 still rely on Ken's behaviour observations for semantic naming.

### Register 20 Bits

Register 20 is a set of alarm bits collated from all packs.  From the values seen, it appears to directly correspond to the bits labelled `BMS xxx` in GivTCP `battery_fault_code` (see [GivTCP register.py](https://github.com/britkat1980/giv_tcp/blob/b6a3ba85c5d81f0acaad0e574fccb902aa23b03c/GivTCP/givenergy_modbus_async/model/register.py#L216))

**Behaviour observations (Ken / GivTCP-derived):**

| Bit | Meaning (if set) | Analysis |
|-----|------------------|----------|
| 1 (lsb) | Over Current | From GivTCP, battery status enum |
| 2 | Short Current | From GivTCP, battery status enum |
| 3 | Over Voltage | From protocol analysis during calibration cycle, seen briefly at end of calibration (max SOC) and from GivTCP, battery status enum |
| 4 | Under Voltage | From protocol analysis during calibration cycle, seen at minimum SOC and from GivTCP, battery status enum |
| 5 | Discharge over temperature | From GivTCP, battery status enum |
| 6 | Charge over temperature | From GivTCP, battery status enum |
| 7 | Discharge under temperature | From GivTCP, battery status enum |
| 8 | Charge under temperature | From GivTCP, battery status enum |
| 9 || Unused? |
| 10 || Unused? |
| 11 || Unused? |
| 12 || Unused? |
| 13 || Unused? |
| 14 || Unused? |
| 15 || Unused? |
| 16 (msb) || Unused? |

**Firmware source mapping (empirical, Unicorn-verified):**

0-indexed (bit N here = 1-indexed bit N+1 above). All 8 bits are taken from three SRAM bytes; HR20 is the OR-composite.

| Bit (0-idx) | Source | Test | Notes |
|----:|--------|------|-------|
| 0 | `*(u8*)0x20000279` | source bit 5 set | Maps to "Over Current" alarm. |
| 1 | `*(u8*)0x20000279` | source bit 6 set | Maps to "Short Current" alarm. |
| 2 | `*(u8*)0x200000D9` | byte != 0 | Maps to "Over Voltage" alarm. |
| 3 | `*(u8*)0x200000D8` | byte != 0 | Maps to "Under Voltage" alarm. |
| 4 | `*(u8*)0x2000027A` | source bit 1 set | Maps to "Discharge over temperature". |
| 5 | `*(u8*)0x2000027A` | source bit 0 set | Maps to "Charge over temperature". |
| 6 | `*(u8*)0x2000027A` | source bit 3 set | Maps to "Discharge under temperature". |
| 7 | `*(u8*)0x2000027A` | source bit 2 set | Maps to "Charge under temperature". |

Note the cross-pair ordering for bits 4-7: the upstream alarm packer at `0x2000027A` writes discharge/charge alarms in an interleaved pattern rather than a simple over/under grouping. The HR20 builder re-orders to the OV/UV/OT/UT layout above.

`0x20000279` / `0x2000027A` are siblings of `0x2000027B` (which contributes to HR19 bits 6/7). All three appear to be packed status bytes from the same upstream aggregator.

#### Writer-traced notes

HR20 bits 3 and 4 (Over-Voltage / Under-Voltage in GivTCP's labelling) source from `*(u8*)0x200000D9` and `*(u8*)0x200000D8` respectively. **Writers fully traced via dynamic Unicorn**: `FUN_0802224C` (the giant pack-walking scheduler-tick that also writes HR11/HR21 mirrors) writes `0x200000D9` at PC `0x08022456` and `0x200000D8` at PC `0x08022442`. The function walks the 6 FC=4 pack slots with stride 145, reads each pack's alarm byte at `pack[0x8D]`, and aggregates. **HR20 bits are therefore global OR-aggregates across all packs**, not per-pack flags. Confirms the prose in this section: "alarm bits collated from all packs".

The remaining HR20 bits (1/2 from `*(u8*)0x20000279`, 5-8 from `*(u8*)0x2000027A`) and HR19 bits 7/8 (from `*(u8*)0x2000027B`) come from **three semantic alarm bitmaps**, each with its own recovery-clearer state machine:

| Byte | Bits feed | Semantic | Setters | Clearer state machine |
|---|---|---|---|---|
| `0x20000279` | HR20 bits 0,1 (Over-Current / Short-Current) | **Current alarm bitmap** | `pace_cid2_dispatch` (driven by AFE alarms over the PACE protocol) + scattered per-protection setters | `FUN_0801ED08` (also clears `0x2000009D`) |
| `0x2000027A` | HR20 bits 4-7 (charge/discharge over/under-temperature) | **Temperature alarm bitmap** | `pace_cid2_dispatch` + per-protection setters | `FUN_0801F02C` (8-bit state machine; per-bit thresholds + 3-cycle recovery, then `*byte &= ~mask`) |
| `0x2000027B` | HR19 bits 6,7 + some HR20 | **Voltage alarm bitmap** | `pace_cid2_dispatch` (primary), `FUN_0801F3F4` | `FUN_0801F3F4` |

All three clearer functions are structurally identical: iterate 8 bits, for each set bit check whether its underlying condition has recovered for 3+ cycles against per-bit thresholds, then clear via `*byte = *byte & ~mask`. **Setters are scattered** -- each fault detector (temperature monitor, voltage threshold checker, etc.) ORs its specific bit into the appropriate byte when triggered, with most "set" paths coming from the AFE chip via PACE protocol frames parsed by `pace_cid2_dispatch`.

This **firmware-side three-byte split (current / temperature / voltage) lines up exactly with the three alarm-category clusters in GivTCP's `battery_fault_code` enum**, which explains why Ken's GivTCP-derived bit labels in the table above are accurate semantic names.

## Field-variation analysis

Across 829 HR responses captured by Ken (over a 3.4-minute cold-start window):

| Type | Count |
|---|---:|
| Registers that are constant | 14 |
| Registers with 2-3 distinct values (state-like) | 4 |
| Registers with slow drift (5-10 distinct values) | 3 |
| Registers with fast variation (85+ distinct values) | 1 (current, reg 23) |

The capture happened with the system in approximately steady state (low current, idle/charging at low rate), which is why most fields didn't transition. **Captures under varied load conditions are needed to lock down the meaning of fields that didn't change here** - especially the bitmask fields (regs 14, 15, 19) and the slow-varying counter (reg 17).

## Reg 11 transition

**Reg 11** changed from `0x00BA` (186) to `0x0174` (372) at 07:23:42.876 - just 3.5 seconds into the capture. With reg 11 understood as **remaining Ah**, this is the BMS reporting that a second 186 Ah pack has come online: `186 -> 372` Ah (one pack -> two packs). The transition coincides with the BMS finishing its boot-time discovery of attached packs.

## Empirical confirmation methodology

The bit-level source mappings for HR15, HR19 and HR20 (and the algorithm for HR17/HR18) were determined by **direct firmware execution in Unicorn Engine**, not from static analysis alone. The setup:

- Load `BMS_ARM.bin` v3022 into Unicorn at flash base `0x08000000` (no rebase).
- Map SRAM `0x20000000` (64 KB) and the relevant STM32F1 peripheral pages.
- Call the FC=3 response builder at flash `0x0801DD7E` directly with R0 = function code, after pre-populating the HR table at `0x200039C0` and any source SRAM bytes of interest.
- Read the TX buffer at `0x200038C0` after return.

For each register under investigation, the harness sweeps the candidate source byte (or bit) and observes which HR bit changes. This isolates the source-to-target mapping unambiguously, free of any whole-firmware initialisation dependencies.

For HR17/HR18, the harness instead seeds the BCD-serial buffer at `0x20000105..A`, runs the upstream copier at flash `0x08001362` (which reverse-byte-copies into `0x20000190..A`), then runs the hash kernel, and compares the result against the wire-observed HR17/HR18 pair. All test cases match.

The harness scripts live in the (private) Ghidra-project workspace at `extensions/unicorn_modbus_v2.py`, `unicorn_hr_fuzz.py`, and `unicorn_followups_v2.py`; they're not in this repo because they depend on the binary which is not redistributable.

The same approach should extend to HR16 / HR21 / HR25 confirmation (already done) and FC=4 per-pack field validation (partial; the FC=4 response builder at `0x0801DEB8` was located but full per-field exercise requires additional state-struct setup that wasn't completed in this pass).

## Audit history

This document has been audited against the firmware three times. The current table is the result of the third pass (2026-05-12) which added empirical Unicorn-based confirmation on top of the static-analysis pass from 2026-05-09. Specific corrections and additions:

**Third pass (2026-05-12), empirical (Unicorn-based execution):**

- **HR15**: previously "OR-mask of 3 conditional bits" with no source mapping -- **bit-to-source-address mapping now confirmed** (see [Register 15 Bits](#register-15-bits)).
- **HR16**: previously "single byte loaded from RAM" with no source -- **source confirmed as `*(u8*)0x20000518`** via direct byte sweep.
- **HR17 / HR18**: previously "iterative shift-accumulate hash of 6 dynamic bytes" with implied liveness -- **clarified that the 6 bytes are device-serial BCD digits (fixed at manufacture), so HR17/18 are a per-device fingerprint, not a runtime hash**. Algorithm verified end-to-end including the reverse-byte copier at `0x08001362`.
- **HR19 bits 6 / 7**: previously mapped to `*(u8*)0x2000027B` bits 5 and 6 -- **corrected to bits 2 and 1 respectively**.
- **HR19 bits 0 / 1 (direction)**: previously "from sign of `*(int*)0x2000014C`" -- **corrected to IEEE-754 equality-with-zero test on the float at `0x2000014C`**. This is why `-0.0f` reports as idle (`01`) rather than discharge.
- **HR20**: previously "set of per-pack online flags" with no bit detail -- **all 8 bits now mapped to source bytes and bit positions** (see [Register 20 Bits](#register-20-bits)).
- **HR21**: previously "possibly SoC" -- **confirmed as direct pass-through of `*(u16*)0x20000184`** (whatever the upstream task writes there appears in HR21 verbatim).
- **HR25**: confirmed as `*(u16)(0x2000153A+8) * 100` (centi-amp scaling) by sweep; matches the static-analysis result.

**Second pass (2026-05-09), static (Ghidra + SVD):**

- **Reg 11**: was `(SoC_float * 100)` -- corrected to **remaining Ah** (whole). The earlier reading correctly identified a `float * something` computation but mis-named the float as SoC; it's actually a centi-Ah accumulator divided by 100.
- **Reg 22**: was labelled "decivolts" -- the math was right (divide by 10) but the source unit was mis-named. Source is mV, output is **0.01 V (centivolts)**.
- **Reg 23**: was `*(float*)0x2000014C * 10.0f` (multiply) -- corrected to `/ 10.0f` (`__aeabi_fdiv`, divide). The operation is unambiguous: helper `0x0802C2A8` has `sub.w r2, r2, r3` (exponent subtraction) and a PC-relative reciprocal lookup table at `addw ip, pc, #0x108` -- canonical software-fdiv markers. **Unit revised 2026-05-13**: the source float at `0x2000014C` is in **milli-amps (mA)**, not centi-amps. So the wire value (HR23 / 10) is **centi-amps (0.01 A)**, not deci-amps. Confirmed by wire trace from Ken's app: a pack drawing ~14.9 A produces HR23 = 1490, which only makes sense as 0.01 A units (149 A in deci-amps is impossible for these batteries). The internal mA representation is for precision (probably to keep the coulomb-counter / calibration math from accumulating rounding error); the `/10` on the wire just truncates to centi-amps to match the inverter's expected protocol unit.
- **Reg 24**: was `(min_cell_mV - 2730) / 10` -- corrected to **max cell temperature in °C** from a 4-element max loop. The first-pass reading had two errors: the loop finds max (not min), and the source array is temperature (raw `°C × 10 + 2730`), not cell voltage.
- **Reg 25**: source was cited as `0x20001598` -- corrected to `*(u16*)(0x2000153A+8)` = PACE slice 1 byte 8.
- **Reg 26 / Reg 27**: were "independent source, tracks each other in steady state" with no semantic -- corrected to **cross-charge current target (charge side / discharge side)**, written by `compute_pack_current_limits` with mirrored ramp-and-converge logic. Tracks each other in steady state by conservation of current at the pack-pair coupling point.

## Inline protocol modification

Using the `modbus_proxy` utility, individual registers can be individually manipulated to observe the external effects on the inverter.  So far, the following experiments have been performed:

| Register | Observation |
|-|-|
| 20 | Modify to 0x04 (Overvoltage) causes inverter to drop charge rate to 0, but has no impact on charge rate.  Modify to 0x08 (under-voltage) causes inverter to limit discharge to 340W. |
| 21 | Directly affects SOC shown in GivEnergy mobile app (iOS) |
| 26 | Directly affects the maximum charge rate by the inverter, shown in GivEnergy mobile app (iOS).  To repro: set inverter to full-rate charge (3000W), set field to `1000` (aka 10.00A), observe displayed charge rate drops to approx 500W. |
| 27 | Directly affects the maximum discharge rate by the inverter, shown in GivEnergy mobile app (iOS).  To repro: set inverter to full-rate discharge (3000W), set field to `1000` (aka 10.00A), observe displayed discharge rate drops to approx 500W. |

## Cross-reference

For the original empirical analysis (raw hex traces, Ken's first-pass interpretations), see [NOTES.md](../NOTES.md) ("Holding Registers" section).

The interpretations above merge Ken's observations with static analysis of the BMS firmware. The relevant flash addresses (under the actual load address `0x08010000`) are: `0x0801D534` = `fc3_init`, `0x0801D584` = `fc3_update_task`, `0x080167BA` = `compute_pack_current_limits`, `0x08014A4C` = `compute_filtered_current` (HR23 source). All populate the HR mirror at SRAM `0x200039C0`.
