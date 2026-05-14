#!/usr/bin/env python3
"""
Extract register values from Modbus request/response pairs into CSV.

Handles GivEnergy's non-standard FC=4 response framing (start-address echo
in place of byte_count). Tracks pending requests to determine FC=4 response
length from the matching request's count.

    Usage:
    python3 extract_fields.py [-g seconds] <log_path> [output.csv] [[w@|sw@|b@]device:fc:reg[+byte_offset] ...]

        device and fc are integers (decimal or 0x-prefixed hex).
        reg is an integer (decimal or 0x-prefixed hex) and may optionally be
        followed by +byte_offset, where byte_offset is a non-negative integer.
        Prefix each spec with:
            - w@  for 16-bit unsigned word output (default if omitted)
            - sw@ for 16-bit signed integer output
            - b@  for 16 separate bit columns per register
    If one or more register specs are given, only those registers appear in
    the output. Omit them to include every observed register.
    Use -g to require a minimum time gap (seconds) between output rows.

Output format:
    - One CSV row per Modbus request/response pair.
    - First column: timestamp (response timestamp).
    - Remaining columns: registers grouped/sorted by device, function code,
      and register address.
    - Each cell contains the last-known value for that register at that row.
    - Consecutive duplicate rows (identical data cells) are suppressed.
"""
import csv
import re
import sys
from datetime import datetime

LINE_RE = re.compile(
    r'^(\S+ \S+)\s+([0-9a-fA-F]+)\s+((?:[0-9a-fA-F]{2} ?)+)\s*\|.*\|\s*$'
)


def crc16(data):
    """Modbus CRC-16, polynomial 0xA001, init 0xFFFF."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def load_byte_stream(path):
    """Read the logger file into (bytes, per-byte timestamps)."""
    stream = bytearray()
    timestamps = []
    with open(path) as f:
        for line in f:
            m = LINE_RE.match(line.rstrip())
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
            raw = bytes.fromhex(m.group(3).replace(' ', ''))
            for b in raw:
                stream.append(b)
                timestamps.append(ts)
    return bytes(stream), timestamps


def parse_frames(stream, timestamps):
    """Walk byte stream parsing Modbus frames.

    GivEnergy's FC=4 response format is non-standard:
        device | FC | start_addr_hi | start_addr_lo | data | CRC
    (no byte_count field; length implicit from request count * 2).

    To compute FC=4 response length we need the count from the matching
    request, so we maintain `pending_req_count` between frames.

    Uses an alternating request/response state machine; falls back to
    swapping role on CRC failure. Resync drops one byte at a time.
    """

    frames = []
    i = 0
    n = len(stream)
    expecting = "request"
    pending_req_count = None
    drops = 0

    while i < n - 3:
        device = stream[i]
        fc = stream[i + 1]

        def try_decode(role, req_count=None):
            if role == "request":
                if fc in (3, 4, 6):
                    L = 8
                else:
                    return None
            else:
                if fc == 3:
                    if i + 2 >= n:
                        return None
                    L = 5 + stream[i + 2]
                elif fc == 4:
                    if req_count is None:
                        return None
                    L = 6 + 2 * req_count
                elif fc == 6:
                    L = 8
                elif fc & 0x80:
                    L = 5
                else:
                    return None
            if L < 4 or i + L > n:
                return None
            frame = bytes(stream[i:i + L])
            rcrc = frame[-2] | (frame[-1] << 8)
            if rcrc == crc16(frame[:-2]):
                return (L, frame)
            return None

        result = try_decode(expecting, pending_req_count)
        used_swap = False
        if result is None:
            other = "response" if expecting == "request" else "request"
            result = try_decode(other, pending_req_count)
            if result is not None:
                used_swap = True
                role = other
            else:
                drops += 1
                i += 1
                continue
        else:
            role = expecting

        L, frame = result
        frames.append({
            "ts": timestamps[i],
            "off": i,
            "raw": frame,
            "role": role,
            "device": device,
            "fc": fc,
            "len": L,
            "swap": used_swap,
        })
        if role == "request" and fc in (3, 4):
            pending_req_count = (frame[4] << 8) | frame[5]
        else:
            pending_req_count = None
        i += L
        expecting = "response" if role == "request" else "request"

    return frames, drops


def pair_request_response(frames):
    """Pair each request with its response.

    A response matches if it is the next frame, same device, and either:
    - same FC (normal response), or
    - FC with exception bit set (request FC | 0x80).
    """
    pairs = []
    for i, f in enumerate(frames):
        if f["role"] != "request":
            continue
        if i + 1 < len(frames):
            nxt = frames[i + 1]
            if nxt["role"] != "response" or nxt["device"] != f["device"]:
                continue
            if nxt["fc"] == f["fc"] or nxt["fc"] == (f["fc"] | 0x80):
                pairs.append((f, nxt))
    return pairs


def column_name(key):
    device, fc, reg = key[:3]
    offset = key[3] if len(key) > 3 else 0
    name = f"device_{device:03d}_fc_{fc:02d}_reg_{reg:05d}"
    if offset:
        name += f"_byte_{offset:03d}"
    return name


def _parse_register_component(part):
    if part == "*":
        return None, 0

    if "+" in part:
        reg_part, offset_part = part.split("+", 1)
        if not reg_part or not offset_part:
            raise ValueError(f"Invalid register component {part!r}")
    else:
        reg_part = part
        offset_part = "0"

    try:
        reg = None if reg_part == "*" else int(reg_part, 0)
    except ValueError:
        raise ValueError(f"Invalid register value {reg_part!r}")

    try:
        offset = int(offset_part, 0)
    except ValueError:
        raise ValueError(f"Invalid byte offset {offset_part!r}")

    if offset < 0:
        raise ValueError(f"Byte offset must be >= 0, got {offset}")

    return reg, offset


def decode_pair_updates(req, rsp):
    """Return register updates for one request/response pair.

    Keys are tuples: (device, fc, register).
    Values are uint16 register values.
    """
    updates = {}
    device = req["device"]
    req_fc = req["fc"]
    rsp_fc = rsp["fc"]
    req_raw = req["raw"]
    rsp_raw = rsp["raw"]

    # Exception response carries no register payload.
    if rsp_fc == (req_fc | 0x80):
        return updates

    if req_fc in (3, 4):
        if len(req_raw) < 8:
            return updates

        start_register = (req_raw[2] << 8) | req_raw[3]
        register_count = (req_raw[4] << 8) | req_raw[5]
        expected_data_bytes = register_count * 2

        if req_fc == 3:
            if len(rsp_raw) < 5 or rsp_raw[2] != expected_data_bytes:
                return updates
            data_start = 3
            if len(rsp_raw) < data_start + expected_data_bytes + 2:
                return updates
        else:
            if len(rsp_raw) < 6:
                return updates
            # GivEnergy FC=4 response echoes start address.
            if rsp_raw[2] != req_raw[2] or rsp_raw[3] != req_raw[3]:
                return updates
            data_start = 4
            if len(rsp_raw) < data_start + expected_data_bytes + 2:
                return updates

        for idx in range(register_count):
            pos = data_start + (2 * idx)
            value = (rsp_raw[pos] << 8) | rsp_raw[pos + 1]
            reg = start_register + idx
            updates[(device, req_fc, reg)] = value

    elif req_fc == 6:
        if len(req_raw) < 8:
            return updates
        reg = (req_raw[2] << 8) | req_raw[3]
        value = (req_raw[4] << 8) | req_raw[5]
        updates[(device, req_fc, reg)] = value

    return updates


def parse_register_filter(specs):
    """Parse filter specs into a list of (mode, pattern) tuples.

    Each spec is one of:
    - device:fc:reg[+byte_offset]
    - w@device:fc:reg[+byte_offset]
    - sw@device:fc:reg[+byte_offset]
    - b@device:fc:reg[+byte_offset]

    Each pattern component may be:
      - a decimal or 0x-prefixed hex integer, or
      - '*' to match any value.

    Returns a list of (mode, pattern) tuples where:
      - mode is 'w', 'sw', or 'b'
    - pattern is a (device, fc, reg, byte_offset) tuple where None represents a wildcard.

    Raises ValueError with a descriptive message on bad input.
    """
    result = []
    for spec in specs:
        mode = "w"
        body = spec

        if "@" in spec:
            prefix, body = spec.split("@", 1)
            if prefix not in ("w", "sw", "b"):
                raise ValueError(
                    f"Unknown format prefix {prefix!r} in {spec!r}; use 'w@', 'sw@', or 'b@'"
                )
            mode = prefix

        parts = body.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"Register spec must be [w@|sw@|b@]device:fc:reg[+byte_offset], got {spec!r}"
            )

        parsed = []
        for label, part in zip(("device", "fc"), parts[:2]):
            if part == "*":
                parsed.append(None)
            else:
                try:
                    parsed.append(int(part, 0))
                except ValueError:
                    raise ValueError(
                        f"Component {label!r} in {spec!r} must be an integer or '*'"
                    )

        reg, offset = _parse_register_component(parts[2])
        parsed.append(reg)
        parsed.append(offset)

        result.append((mode, tuple(parsed)))

    return result


def _key_matches_pattern(key, pattern):
    """Return True if key matches a single pattern.

    Pattern is (device, fc, reg, byte_offset) where None is a wildcard.
    """
    return all(p is None or p == k for p, k in zip(pattern[:3], key))


def _field_registers(field_key):
    """Return the raw register keys required to materialize a field key."""
    device, fc, reg, offset = field_key
    start_reg = reg + (offset // 2)
    end_reg = reg + ((offset + 1) // 2)
    return [(device, fc, register) for register in range(start_reg, end_reg + 1)]


def _field_byte(last_known, field_key, byte_offset):
    """Return one byte from a field key, or None if the source register is missing."""
    device, fc, reg, offset = field_key
    absolute = offset + byte_offset
    register = reg + (absolute // 2)
    key = (device, fc, register)
    value = last_known.get(key)
    if value is None or value == "":
        return None
    if absolute % 2 == 0:
        return (value >> 8) & 0xFF
    return value & 0xFF


def _field_word(last_known, field_key):
    """Return a uint16 extracted from a field key, or None if incomplete."""
    hi = _field_byte(last_known, field_key, 0)
    lo = _field_byte(last_known, field_key, 1)
    if hi is None or lo is None:
        return None
    return (hi << 8) | lo


def _column_specs(all_keys, register_filter):
    """Build ordered CSV column specs based on discovered keys and filter.

    Returns list of tuples:
      - ("w", key) for word columns
      - ("b", key, bit_index) for bit columns (bit 15 down to 0)

    The key is a (device, fc, reg, byte_offset) tuple. For register-aligned
    columns, byte_offset is 0.
    """
    sorted_keys = sorted(all_keys)

    if register_filter is None:
        return [("w", key + (0,)) for key in sorted_keys]

    word_keys = set()
    signed_word_keys = set()
    bit_keys = set()

    for key in sorted_keys:
        for mode, pattern in register_filter:
            if not _key_matches_pattern(key, pattern):
                continue
            field_key = key + (pattern[3],)
            needed_keys = _field_registers(field_key)
            if any(required_key not in all_keys for required_key in needed_keys):
                continue
            if mode == "b":
                bit_keys.add(field_key)
            elif mode == "sw":
                signed_word_keys.add(field_key)
            else:
                word_keys.add(field_key)

    specs = []
    for key in sorted(word_keys | signed_word_keys | bit_keys):
        if key in word_keys:
            specs.append(("w", key))
        if key in signed_word_keys:
            specs.append(("sw", key))
        if key in bit_keys:
            for bit_index in range(0, 16):
                specs.append(("b", key, bit_index))

    return specs


def write_register_state_csv(pairs, out, register_filter=None, min_gap_seconds=0.0):
    """Write one row per pair with last-known register values.

    register_filter, if given, is a list of (mode, pattern) tuples.
    min_gap_seconds, if > 0, suppresses rows whose timestamps are too close
    to the previous emitted row.
    """
    pair_updates = []
    all_keys = set()

    for req, rsp in pairs:
        updates = decode_pair_updates(req, rsp)
        pair_updates.append((rsp["ts"], updates))
        all_keys.update(updates.keys())

    col_specs = _column_specs(all_keys, register_filter)
    header = ["timestamp"]
    for spec in col_specs:
        if spec[0] in ("w", "sw"):
            header.append(column_name(spec[1]))
        else:
            header.append(f"{column_name(spec[1])}_bit_{spec[2] + 1:02d}")

    writer = csv.writer(out)
    writer.writerow(header)

    last_known = {}
    last_data_row = None
    last_output_ts = None
    for ts, updates in pair_updates:
        if updates:
            last_known.update(updates)

        data_row = []
        bit_col_num = 0
        for spec in col_specs:
            if spec[0] == "w":
                value = _field_word(last_known, spec[1])
                if value is None:
                    data_row.append("")
                else:
                    data_row.append(value)
            elif spec[0] == "sw":
                value = _field_word(last_known, spec[1])
                if value is None:
                    data_row.append("")
                else:
                    data_row.append(value if value < 0x8000 else value - 0x10000)
            else:
                key = spec[1]
                bit_index = spec[2]
                value = _field_word(last_known, key)
                if value is None:
                    data_row.append("")
                else:
                    raw_bit = (value >> bit_index) & 1
                    data_row.append(bit_col_num * 2 + raw_bit)
                bit_col_num += 1

        if data_row == last_data_row:
            continue

        if min_gap_seconds > 0 and last_output_ts is not None:
            dt = (ts - last_output_ts).total_seconds()
            if dt < min_gap_seconds:
                continue

        last_data_row = data_row
        last_output_ts = ts

        row = [ts.strftime("%Y-%m-%d %H:%M:%S.%f")] + data_row
        writer.writerow(row)


def main(argv):
    args = argv[1:]
    min_gap_seconds = 0.0
    positional = []

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-g"):
            if i + 1 >= len(args):
                print("Error: -g requires a numeric value in seconds", file=sys.stderr)
                return 2
            try:
                min_gap_seconds = float(args[i + 1])
            except ValueError:
                print(f"Error: invalid gap value {args[i + 1]!r}; must be numeric seconds", file=sys.stderr)
                return 2
            i += 2
            continue
        if arg.startswith("-"):
            print(f"Error: unknown option {arg!r}", file=sys.stderr)
            return 2
        positional.append(arg)
        i += 1

    if min_gap_seconds < 0:
        print("Error: -g must be >= 0", file=sys.stderr)
        return 2

    if len(positional) < 1:
        print(
            f"Usage: {argv[0]} [-g seconds] <log_path> [output.csv] [[w@|sw@|b@]device:fc:reg[+byte_offset] ...]",
            file=sys.stderr,
        )
        return 2

    path = positional[0]

    # argv[2] is the optional output file if it doesn't look like a register spec.
    # Register specs always contain ':', so use that to distinguish.
    rest = positional[1:]
    if rest and ":" not in rest[0]:
        out_path = rest[0]
        filter_specs = rest[1:]
    else:
        out_path = None
        filter_specs = rest

    register_filter = None
    if filter_specs:
        try:
            register_filter = parse_register_filter(filter_specs)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    stream, timestamps = load_byte_stream(path)
    frames, drops = parse_frames(stream, timestamps)
    pairs = pair_request_response(frames)

    if out_path is not None:
        with open(out_path, 'w', newline='') as f:
            write_register_state_csv(pairs, f, register_filter, min_gap_seconds=min_gap_seconds)
        print(f"CSV written to {out_path} ({len(pairs)} pairs, {len(frames)} frames, {drops} dropped bytes)", file=sys.stderr)
    else:
        write_register_state_csv(pairs, sys.stdout, register_filter, min_gap_seconds=min_gap_seconds)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
