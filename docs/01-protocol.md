# Protocol fundamentals

GivEnergy LV BMSes speak Modbus-RTU on RS485, with one important non-standard quirk in the FC=4 response format.

## Physical layer

| Parameter | Value |
|---|---|
| Bus | RS485, multidrop |
| Baud rate | 9600 |
| Framing | 8N1 (8 data bits, no parity, 1 stop bit) |
| Inter-frame silence | Standard Modbus-RTU (>=3.5 char times = ~3.6 ms at 9600) |

The bus is multidrop, so multiple devices can share it. A single inverter polls multiple battery slaves on one cable.

Slave addresses are set per-battery via dipswitches; the value of the dipswitches is used directly as the Modbus slave ID. Typical configurations use slaves 1..5 for up to five paralleled batteries; the inverter polls all slave addresses 1..5 even if only some are populated (see [docs/03-input-registers.md](03-input-registers.md) for the "absent slave" pattern).

## Function-code support

The BMS firmware implements only **three** Modbus function codes:

| FC | Name | Direction | Use |
|---|---|---|---|
| `0x03` | Read Holding Registers | inverter -> BMS | Status / config registers (HR poll) |
| `0x04` | Read Input Registers | inverter -> BMS | Telemetry registers (cells, capacities, temps) |
| `0x06` | Write Single Holding Register | inverter -> BMS | Mode-change commands (rare; not seen in steady-state polling) |

Any other FC produces a Modbus exception response with code `1` ("Illegal Function"). This was confirmed by static analysis of the BMS firmware (the dispatcher hard-codes a `cmp #3 / cmp #4 / cmp #6` chain before defaulting to the exception path).

Maximum register count per request is `0x80` (128). Exceeding this returns exception code `2` for FC=3 or `4` for FC=4.

## CRC

Standard Modbus CRC-16:

- Polynomial: `0xA001` (reverse of 0x8005)
- Initial value: `0xFFFF`
- Appended to every frame as **low byte first, high byte second**

```python
def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF
```

The BMS firmware uses the canonical Modbus auchCRCHi / auchCRCLo lookup tables internally. CRC failure causes the BMS to silently drop the frame (no exception response).

## Request format

All Modbus requests from the inverter to the BMS are 8 bytes:

```
[slave_addr] [FC] [addr_hi] [addr_lo] [count_hi or value_hi] [count_lo or value_lo] [crc_lo] [crc_hi]
```

For FC=3 and FC=4, bytes 4-5 are the register count. For FC=6, they are the register value to write.

## Response format - FC=3 (standard)

The HR-poll response uses standard Modbus framing:

```
[slave_addr] [0x03] [byte_count] [data...] [crc_lo] [crc_hi]
```

`byte_count` = count x 2.

## Response format - FC=4 (NON-STANDARD)

The IR-poll response does **not** include a byte_count field. Instead, it echoes the request's start address (2 bytes, big-endian):

```
[slave_addr] [0x04] [addr_echo_hi] [addr_echo_lo] [data...] [crc_lo] [crc_hi]
```

Data length is implicit from the request's count x 2.

This is the most important pitfall for emulator implementations. **A stock Modbus library will produce standard FC=4 responses with byte_count, which the inverter will reject** (CRC mismatch because the byte at offset 2 differs from what the inverter computed).

### Confirming the format

The format is visible in the wire captures (Ken's `cold_start.log`). For example, an IR Block 2 exchange:

- Request: `01 04 00 15 00 13 a0 03` - slave=1, FC=4, start=`0x0015`, count=`0x0013` (19 regs), CRC
- Response: `01 04 00 15 10 02 e1 ...` - slave=1, FC=4, **`00 15`** = echoed start address (NOT byte_count), followed by data

If the format were standard, byte 2 of the response would be `0x26` (=38, the byte count for 19 registers). Instead it is `0x00`, and byte 3 is `0x15` (the request's start address low byte). Across all observed FC=4 responses, the value at bytes 2-3 is always exactly equal to the request's start address.

FC=3 (HR) responses **do** use the standard format with byte_count - only FC=4 differs.

## Response format - FC=6

FC=6 responses echo the request frame back unchanged (standard Modbus behaviour for write-single).

Only seen at boot or during user-initiated mode changes (charge enable, BMS reset, force-charge); not in steady-state polling. Read-only emulators must still recognise FC=6 requests and produce the echo response or the inverter's command will retry indefinitely.

## Direction handling

RS485 is half-duplex; the bus owner must drive the line direction (DE/RE) appropriately. The inverter side handles this via a GPIO toggled around its TX. For an emulator using a standard RS485 dongle (e.g. Waveshare USB-RS485), the dongle's auto-DE feature usually handles this transparently.

## See also

- [02-holding-registers.md](02-holding-registers.md) - HR(0..27) layout
- [03-input-registers.md](03-input-registers.md) - IR Block 1/2/3 layouts
- [06-wire-captures.md](06-wire-captures.md) - timing, cadence, capture methodology
- [07-emulator-implications.md](07-emulator-implications.md) - design rules for emulators
