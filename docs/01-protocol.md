# Protocol fundamentals

GivEnergy LV BMSes speak Modbus-RTU on RS485, with one important non-standard quirk in the FC=4 response format.

## Physical layer

| Parameter | Value |
|---|---|
| Bus | RS485, multidrop |
| Baud rate | 9600 |
| Framing | 8N1 (8 data bits, no parity, 1 stop bit) |
| Inter-frame silence | Standard Modbus-RTU (>=3.5 char times = ~3.6 ms at 9600) |

The bus is multidrop, so multiple devices can share it. A single inverter polls multiple battery devices on one cable.

Device addresses are set per-battery via dipswitches; the value of the dipswitches is used directly as the Modbus device ID. Typical configurations use devices 1..5 for up to five paralleled batteries; the inverter polls all device addresses 1..5 even if only some are populated (see [docs/03-input-registers.md](03-input-registers.md) for the "absent device" pattern).

## Function-code support

The BMS firmware implements only **three** Modbus function codes:

| FC | Name | Direction | Use |
|---|---|---|---|
| `0x03` | Read Holding Registers | inverter -> BMS | Status / config registers (HR poll) |
| `0x04` | Read Input Registers | inverter -> BMS | Telemetry registers (cells, capacities, temps) |
| `0x06` | Write Single Holding Register | inverter -> BMS | Mode-change commands (not seen in any capture so far) |

Any other FC produces a Modbus exception response with code `1` ("Illegal Function"). This was confirmed by static analysis of the BMS firmware (the dispatcher hard-codes a `cmp #3 / cmp #4 / cmp #6` chain before defaulting to the exception path).

Maximum register count per request is `0x80` (128). Exceeding this returns exception code `2` for FC=3. For FC=4 the FC=4 handler has a tighter check of its own: at most 60 registers, within one 60-register block. A request that fails it gets a short non-standard reply rather than a normal exception (see [03-input-registers.md](03-input-registers.md#validation-envelope-firmware-imposed)).

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
[device_addr] [FC] [addr_hi] [addr_lo] [count_hi or value_hi] [count_lo or value_lo] [crc_lo] [crc_hi]
```

For FC=3 and FC=4, bytes 4-5 are the register count. For FC=6, they are the register value to write.

## Response format - FC=3 (standard)

The HR-poll response uses standard Modbus framing:

```
[device_addr] [0x03] [byte_count] [data...] [crc_lo] [crc_hi]
```

`byte_count` = count x 2.

## Response format - FC=4 (NON-STANDARD)

The IR-poll response does **not** include a byte_count field. Instead, it echoes the request's start address (2 bytes, big-endian):

```
[device_addr] [0x04] [addr_echo_hi] [addr_echo_lo] [data...] [crc_lo] [crc_hi]
```

Data length is implicit from the request's count x 2.

This is the most important pitfall for emulator implementations. **A stock Modbus library will produce standard FC=4 responses with byte_count, which the inverter will reject.** On a G3 LV the DSP works out the reply length from its own request, `(count + 3) x 2` bytes for FC=4, which is one byte more than a standard reply. A standard reply never reaches that length and is dropped at the next poll (see [05-inverter-firmware.md](05-inverter-firmware.md#reply-acceptance)).

### Confirming the format

The format is visible in the wire captures (Ken's `cold_start.log`). For example, an IR Block 2 exchange:

- Request: `01 04 00 15 00 13 a0 03` - device=1, FC=4, start=`0x0015`, count=`0x0013` (19 regs), CRC
- Response: `01 04 00 15 10 02 e1 ...` - device=1, FC=4, **`00 15`** = echoed start address (NOT byte_count), followed by data

If the format were standard, byte 2 of the response would be `0x26` (=38, the byte count for 19 registers). Instead it is `0x00`, and byte 3 is `0x15` (the request's start address low byte). Across all observed FC=4 responses, the value at bytes 2-3 is always exactly equal to the request's start address.

FC=3 (HR) responses **do** use the standard format with byte_count - only FC=4 differs.

## Response format - FC=6

FC=6 responses are not the standard Modbus echo. The BMS firmware shows what the battery sends:

- **Firmware 3020 and 3022** (the Gen 1 and Gen 2 LV batteries): 5 bytes. The replying pack's own address, `06`, a status byte and the CRC. The status byte is always 0, for registers the battery ignores and for out-of-range values too. The master pack sends `01 06 00 23 A0`, whichever device the write was addressed to.
- **Firmware 4009 to 4011** (the Gen 3 LV battery): 8 bytes. The address, `06`, the register, two bytes that are 0 except in one case of `00 01`, and the CRC. That is neither an echo nor the 5-byte frame.

An earlier version of this page said the battery echoes the request. That was an assumption.

No FC=6 request appears in Ken's capture, the joined data of the 90-hour G3 capture or my own G3 captures (43 days of wire capture between 26 September and 24 November 2026, which hold no request other than the polls and no exception reply). The G3 LV DSP can send FC=6 writes to BMS registers 1 to 4 on internal counters, and it ignores the reply: it accepts only FC=3 and FC=4 replies, so the form of the reply makes no difference on a G3. An emulator should answer FC=6 as the battery it presents itself as does. An earlier version of this page said the inverter retries indefinitely without an echo; I have found no capture or firmware evidence for that, and on a G3 it is not the case.

## Direction handling

RS485 is half-duplex; the bus owner must drive the line direction (DE/RE) appropriately. The inverter side handles this via a GPIO toggled around its TX. For an emulator using a standard RS485 dongle (e.g. Waveshare USB-RS485), the dongle's auto-DE feature usually handles this transparently.

## See also

- [02-holding-registers.md](02-holding-registers.md) - HR(0..27) layout
- [03-input-registers.md](03-input-registers.md) - IR Block 1/2/3 layouts
- [06-wire-captures.md](06-wire-captures.md) - timing, cadence, capture methodology
- [07-emulator-implications.md](07-emulator-implications.md) - design rules for emulators
