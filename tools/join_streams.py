"""Join wire + TCP + tags streams from a campaign capture into a single parquet."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Sibling modules (parse_log is the existing wire-frame parser)
sys.path.insert(0, str(Path(__file__).parent))
import parse_log  # noqa: E402
import decode_fields  # noqa: E402


def load_wire_records(wire_path: Path) -> pd.DataFrame:
    """Decode wire.log into a DataFrame, one row per request/response pair."""
    stream, timestamps = parse_log.load_byte_stream(str(wire_path))
    frames, _ = parse_log.parse_frames(stream, timestamps)
    pairs = parse_log.pair_request_response(frames)
    rows = []
    for req, rsp in pairs:
        row = {
            "ts": pd.Timestamp(rsp["ts"]),
            "device": rsp["device"],
            "fc": rsp["fc"],
            "addr": (req["raw"][2] << 8) | req["raw"][3],
            "count": (req["raw"][4] << 8) | req["raw"][5] if rsp["fc"] in (3, 4) else None,
        }
        decoded = decode_fields.decode_response(rsp, req)
        row.update(decoded)
        rows.append(row)
    return pd.DataFrame(rows)


def load_tcp_records(tcp_path: Path) -> pd.DataFrame:
    """Decode tcp.ndjson into a DataFrame, one row per poll, columns prefixed tcp_."""
    rows = []
    with open(tcp_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            row = {"ts": pd.Timestamp(rec["ts"])}
            for k, v in rec.get("fields", {}).items():
                row[f"tcp_{k}"] = v
            rows.append(row)
    return pd.DataFrame(rows)


def load_tag_records(tags_path: Path) -> pd.DataFrame:
    """Decode tags.ndjson into a DataFrame of sparse events."""
    rows = []
    with open(tags_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append({
                "ts": pd.Timestamp(rec["ts"]),
                "tag": rec["tag"],
                "source": rec.get("source", "manual"),
            })
    return pd.DataFrame(rows)


def join_streams(wire: pd.DataFrame, tcp: pd.DataFrame, tags: pd.DataFrame) -> pd.DataFrame:
    """Time-align wire frames with forward-filled TCP and active tag.

    merge_asof requires both sides sorted by ts. Direction "backward" finds
    the most recent left-side timestamp <= each wire-side timestamp.
    """
    wire = wire.sort_values("ts").reset_index(drop=True) if not wire.empty else wire

    if not tcp.empty:
        tcp = tcp.sort_values("ts").reset_index(drop=True)
        joined = pd.merge_asof(wire, tcp, on="ts", direction="backward")
    else:
        joined = wire.copy()

    if not tags.empty:
        tags_sorted = tags.sort_values("ts").reset_index(drop=True)
        joined = pd.merge_asof(joined, tags_sorted[["ts", "tag", "source"]],
                               on="ts", direction="backward")
        joined = joined.rename(columns={"tag": "active_tag", "source": "tag_source"})
    else:
        joined["active_tag"] = pd.NA
        joined["tag_source"] = pd.NA

    return joined


def main():
    p = argparse.ArgumentParser(description="Join wire + TCP + tag streams into parquet")
    p.add_argument("--wire", type=Path, required=True)
    p.add_argument("--tcp", type=Path, required=True)
    p.add_argument("--tags", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    wire = load_wire_records(args.wire)
    tcp = load_tcp_records(args.tcp)
    tags = load_tag_records(args.tags) if args.tags else pd.DataFrame()

    joined = join_streams(wire, tcp, tags)
    joined.to_parquet(args.out)
    print(f"Wrote {len(joined)} rows to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
