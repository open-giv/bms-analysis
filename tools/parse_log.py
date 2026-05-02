#!/usr/bin/env python3
"""
Parse output from serial_hexdump_logger.c into structured Modbus frames.

Handles GivEnergy's non-standard FC=4 response framing (start-address echo
in place of byte_count). Tracks pending requests to determine FC=4 response
length from the matching request's count.

Usage:
    python3 parse_log.py path/to/capture.log [output.md]

If no output path is given, writes a summary to stdout.

Reads the line format produced by serial_hexdump_logger.c:

    2026-05-01 07:23:39.416  00000000  01 03 00 00 00 1C 44 03   |......D.|
    timestamp                offset    hex bytes (up to 16)      ASCII

Bytes from consecutive lines that share a timestamp belong to the same
flush. Modbus frames may span multiple flushes (~1 ms apart) - the parser
reassembles by walking the byte stream and using the FC byte to determine
expected frame length.
"""
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median

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
        slave | FC | start_addr_hi | start_addr_lo | data | CRC
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
        slave = stream[i]
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
            "slave": slave,
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
    """Pair each request with its response (the next frame, same slave/FC)."""
    pairs = []
    for i, f in enumerate(frames):
        if f["role"] != "request":
            continue
        if i + 1 < len(frames):
            nxt = frames[i + 1]
            if nxt["role"] == "response" and nxt["slave"] == f["slave"] and nxt["fc"] == f["fc"]:
                pairs.append((f, nxt))
    return pairs


def report(frames, drops, pairs, out=sys.stdout):
    """Write a structured report."""
    p = lambda s="": print(s, file=out)

    if frames:
        ts_first = frames[0]["ts"]
        ts_last = frames[-1]["ts"]
        span = (ts_last - ts_first).total_seconds()
    else:
        span = 0

    p("# Modbus capture analysis")
    p()
    p(f"- Total frames decoded: {len(frames)}")
    p(f"- Bytes dropped during resync: {drops}")
    p(f"- Capture span: {span:.1f} s")
    p(f"- Request->response pairs: {len(pairs)}")
    p()

    # Frame summary
    roles = Counter()
    for f in frames:
        roles[(f["role"], f["fc"])] += 1
    p("## Frame summary")
    p()
    p("| Role | FC | Count |")
    p("|---|---:|---:|")
    for (role, fc), n in sorted(roles.items()):
        p(f"| {role} | {fc} | {n} |")
    p()

    # Cadence
    by_type = defaultdict(list)
    for req, _ in pairs:
        if req["fc"] in (3, 4):
            raw = req["raw"]
            start = (raw[2] << 8) | raw[3]
            count = (raw[4] << 8) | raw[5]
            by_type[(req["slave"], req["fc"], start, count)].append(req["ts"])

    p("## Query types and cadences")
    p()
    p("| Slave | FC | Start | Count | Reqs | Avg gap (ms) | Min | Max |")
    p("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key, tss in sorted(by_type.items(), key=lambda x: (-len(x[1]), x[0])):
        slave, fc, start, count = key
        if len(tss) >= 2:
            deltas = [(tss[i + 1] - tss[i]).total_seconds() * 1000 for i in range(len(tss) - 1)]
            p(f"| 0x{slave:02x} | {fc} | 0x{start:04x} | {count} | {len(tss)} | {mean(deltas):.1f} | {min(deltas):.0f} | {max(deltas):.0f} |")
        else:
            p(f"| 0x{slave:02x} | {fc} | 0x{start:04x} | {count} | {len(tss)} | - | - | - |")
    p()

    # Latency
    p("## Request -> response latency (BMS turnaround)")
    p()
    latencies = defaultdict(list)
    for req, rsp in pairs:
        dt_ms = (rsp["ts"] - req["ts"]).total_seconds() * 1000
        if req["fc"] in (3, 4):
            start = (req["raw"][2] << 8) | req["raw"][3]
            count = (req["raw"][4] << 8) | req["raw"][5]
            key = (req["slave"], req["fc"], start, count)
        else:
            key = (req["slave"], req["fc"])
        latencies[key].append(dt_ms)

    p("| Query | n | avg ms | p50 | min | max |")
    p("|---|---:|---:|---:|---:|---:|")
    for key, lats in sorted(latencies.items(), key=lambda x: -len(x[1]))[:30]:
        if len(key) == 4:
            slave, fc, start, count = key
            label = f"slave 0x{slave:02x} FC{fc} start=0x{start:04x} cnt={count}"
        else:
            slave, fc = key
            label = f"slave 0x{slave:02x} FC{fc}"
        if len(lats) >= 2:
            p(f"| {label} | {len(lats)} | {mean(lats):.1f} | {median(lats):.1f} | {min(lats):.1f} | {max(lats):.1f} |")
        else:
            p(f"| {label} | 1 | {lats[0]:.1f} | - | - | - |")
    p()

    # Anomalies
    fc6 = [f for f in frames if f["fc"] == 6 and f["role"] == "request"]
    excs = [f for f in frames if f["fc"] & 0x80]
    if fc6:
        p(f"## FC=06 writes: {len(fc6)}")
        p()
        for f in fc6[:20]:
            raw = f["raw"]
            addr = (raw[2] << 8) | raw[3]
            val = (raw[4] << 8) | raw[5]
            p(f"- {f['ts'].strftime('%H:%M:%S.%f')[:-3]}  slave=0x{f['slave']:02x} addr=0x{addr:04x} value=0x{val:04x}")
        p()
    if excs:
        p(f"## Modbus exceptions: {len(excs)}")
        p()
        for f in excs[:20]:
            p(f"- {f['ts'].strftime('%H:%M:%S.%f')[:-3]}  slave=0x{f['slave']:02x} fc=0x{f['fc']:02x} code={f['raw'][2]}")


def main(argv):
    if len(argv) < 2:
        print(f"Usage: {argv[0]} <log_path> [output.md]", file=sys.stderr)
        return 2
    path = argv[1]
    stream, timestamps = load_byte_stream(path)
    frames, drops = parse_frames(stream, timestamps)
    pairs = pair_request_response(frames)
    if len(argv) >= 3:
        with open(argv[2], 'w') as f:
            report(frames, drops, pairs, f)
        print(f"Report written to {argv[2]}", file=sys.stderr)
    else:
        report(frames, drops, pairs)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
