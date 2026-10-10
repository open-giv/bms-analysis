"""Privacy filter -- strip private identifiers from capture artefacts.

Operates on text (logs, NDJSON, CSV), parquet (column by column, through pyarrow) and raw
bytes (wire captures). Reads config from ~/.givenergy-redact.toml or env vars
GIVE_REDACT_SERIALS and GIVE_REDACT_IPS (comma-separated).

A parquet file can't be searched as raw bytes: its columns are encoded and usually compressed, so
a serial may not appear in the file as plain text even though every row holds it. Parquet is
therefore read, redacted value by value in its string and binary columns, and written back with
the same schema.

Refuses to run with no serials or IPs configured, since it would only copy the input. Reports how
many replacements it made.

Idempotent: re-running on already-redacted output produces the same output.
"""
import argparse
import gzip
import os
import sys
from pathlib import Path
from typing import Any, Dict


def load_config_dict(path: Path | None = None) -> Dict[str, Any]:
    """Load redaction config. File takes precedence over env vars."""
    if path is None:
        path = Path.home() / ".givenergy-redact.toml"
    if path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return {
            "serials": list(data.get("serials", [])),
            "ips": list(data.get("ips", [])),
        }
    return {
        "serials": [s for s in os.environ.get("GIVE_REDACT_SERIALS", "").split(",") if s],
        "ips": [s for s in os.environ.get("GIVE_REDACT_IPS", "").split(",") if s],
    }


def redact_text(text: str, config: Dict[str, Any]) -> str:
    """Replace serials and IPs in arbitrary text. Handles ASCII and hex-encoded
    forms of each serial.
    """
    out = text
    for serial in config.get("serials", []):
        if not serial:
            continue
        out = out.replace(serial, "X" * len(serial))
        for case in (lambda c: f"{ord(c):02x}", lambda c: f"{ord(c):02X}"):
            hex_form = " ".join(case(c) for c in serial)
            out = out.replace(hex_form, " ".join(["XX"] * len(serial)))
    for ip in config.get("ips", []):
        if not ip:
            continue
        out = out.replace(ip, "X.X.X.X")
    return out


def redact_bytes(payload: bytes, config: Dict[str, Any]) -> bytes:
    """Replace serial substrings inside raw byte payloads."""
    out = payload
    for serial in config.get("serials", []):
        if not serial:
            continue
        needle = serial.encode("ascii")
        replacement = b"X" * len(needle)
        out = out.replace(needle, replacement)
    return out


PARQUET_MAGIC = b"PAR1"


def count_matches(text: str, config: Dict[str, Any]) -> int:
    """How many serials (ASCII or hex form) and IPs appear in text."""
    n = 0
    for serial in config.get("serials", []):
        if not serial:
            continue
        # A serial with no hex letters has the same lower- and upper-case hex form: count it once.
        forms = {serial} | {" ".join(f"{ord(c):{fmt}}" for c in serial) for fmt in ("02x", "02X")}
        n += sum(text.count(f) for f in forms)
    for ip in config.get("ips", []):
        if ip:
            n += text.count(ip)
    return n


def count_matches_bytes(payload: bytes, config: Dict[str, Any]) -> int:
    return sum(payload.count(s.encode("ascii")) for s in config.get("serials", []) if s)


def redact_parquet(in_path: Path, out_path: Path, config: Dict[str, Any]) -> int:
    """Redact every string and binary column of a parquet file. Returns the replacement count."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(in_path)
    count = 0
    columns = []
    for field, column in zip(table.schema, table.columns):
        t = field.type
        if pa.types.is_string(t) or pa.types.is_large_string(t):
            values = column.to_pylist()
            count += sum(count_matches(v, config) for v in values if v is not None)
            column = pa.chunked_array([[None if v is None else redact_text(v, config) for v in values]], type=t)
        elif pa.types.is_binary(t) or pa.types.is_large_binary(t):
            values = column.to_pylist()
            count += sum(count_matches_bytes(v, config) for v in values if v is not None)
            column = pa.chunked_array([[None if v is None else redact_bytes(v, config) for v in values]], type=t)
        elif pa.types.is_dictionary(t) and (pa.types.is_string(t.value_type) or pa.types.is_large_string(t.value_type)):
            values = column.cast(t.value_type).to_pylist()
            count += sum(count_matches(v, config) for v in values if v is not None)
            column = pa.chunked_array([[None if v is None else redact_text(v, config) for v in values]], type=t.value_type).cast(t)
        columns.append(column)
    pq.write_table(pa.Table.from_arrays(columns, schema=table.schema), out_path)
    return count


def redact_file(in_path: Path, out_path: Path, config: Dict[str, Any]) -> int:
    """Redact a file, choosing text vs bytes based on extension.

    A gzipped file (as givcap-compress leaves finished days) is decompressed, redacted by the
    extension of the name inside it (wire.log.gz and wire.log.gz.redacted both count as .log),
    and compressed again. Searching the compressed bytes would find nothing.

    A parquet file (found by its magic bytes) is redacted column by column. Returns the number of
    replacements made.
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    text_exts = {".log", ".ndjson", ".json", ".md", ".csv", ".txt"}
    data = in_path.read_bytes()
    if data[:4] == PARQUET_MAGIC:
        return redact_parquet(in_path, out_path, config)
    if data[:2] == b"\x1f\x8b":
        inner = [s.lower() for s in in_path.suffixes if s.lower() not in (".gz", ".redacted")]
        data = gzip.decompress(data)
        if inner and inner[-1] in text_exts:
            text = data.decode("utf-8")
            count = count_matches(text, config)
            data = redact_text(text, config).encode("utf-8")
        else:
            count = count_matches_bytes(data, config)
            data = redact_bytes(data, config)
        out_path.write_bytes(gzip.compress(data))
        return count
    if in_path.suffix.lower() in text_exts:
        text = in_path.read_text()
        out_path.write_text(redact_text(text, config))
        return count_matches(text, config)
    out_path.write_bytes(redact_bytes(data, config))
    return count_matches_bytes(data, config)


def main():
    p = argparse.ArgumentParser(description="Redact private identifiers from capture artefacts")
    p.add_argument("input", type=Path)
    p.add_argument("--out", type=Path, help="Output path (default: in-place with .redacted suffix)")
    p.add_argument("--config", type=Path, help="Override config path")
    args = p.parse_args()
    cfg = load_config_dict(args.config)
    if not any(cfg.get("serials", [])) and not any(cfg.get("ips", [])):
        sys.exit(
            "No serials or IPs configured, so nothing would be redacted. "
            "Add them to ~/.givenergy-redact.toml, or set GIVE_REDACT_SERIALS / GIVE_REDACT_IPS."
        )
    out = args.out or args.input.with_suffix(args.input.suffix + ".redacted")
    count = redact_file(args.input, out, cfg)
    print(f"Wrote {out} ({count} replacement{'s' if count != 1 else ''})", file=sys.stderr)


if __name__ == "__main__":
    main()
