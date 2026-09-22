"""Health report for a capture box: services, data freshness, disk, clock.

Run on the Pi as `givcap-status` (capture-box/givcap-status wraps this file).
"""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

CAPTURES = Path.home() / "captures"
SERVICE_NAMES = ("givcap-wire", "givcap-mqtt")
LOW_DISK_BYTES = 2 * 10**9
TAIL_BYTES = 8192


def _parse_line_time(line: str) -> datetime | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            ts = datetime.fromisoformat(json.loads(line)["ts"])
        except (ValueError, KeyError, TypeError):
            return None
    else:
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            ts = datetime.fromisoformat(f"{parts[0]} {parts[1]}")
        except ValueError:
            return None
    return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def last_line_time(path: Path) -> datetime | None:
    """Timestamp of the last complete, parseable line in a wire.log or tcp.ndjson file.

    Reads backwards from the end in growing windows, because one tcp.ndjson
    snapshot line can be longer than any fixed window.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            window = TAIL_BYTES
            while True:
                start = max(0, size - window)
                f.seek(start)
                lines = f.read().decode("utf-8", errors="replace").splitlines()
                if start > 0:
                    lines = lines[1:]  # the first line in the window may be cut off
                for line in reversed(lines):
                    ts = _parse_line_time(line)
                    if ts is not None:
                        return ts
                if start == 0:
                    return None
                window *= 4
    except FileNotFoundError:
        return None


def report(captures_dir: Path, now: datetime, services: dict[str, str],
           free_bytes: int, clock_synced: str) -> list[str]:
    day = now.strftime("%Y-%m-%d")
    lines = [f"{name}: {state}" for name, state in services.items()]
    for filename in ("wire.log", "tcp.ndjson"):
        path = captures_dir / day / filename
        ts = last_line_time(path)
        if ts is None and not path.exists():
            lines.append(f"{filename}: no file yet for {day}")
        elif ts is None:
            lines.append(f"{filename}: exists but no readable timestamp")
        else:
            age = int((now - ts).total_seconds())
            lines.append(f"{filename}: last line {ts:%Y-%m-%d %H:%M:%S} UTC ({age} s ago)")
    lines.append(f"disk free: {free_bytes / 10**9:.1f} GB")
    if free_bytes < LOW_DISK_BYTES:
        lines.append("WARNING: less than 2 GB free")
    lines.append(f"clock synchronised: {clock_synced}")
    return lines


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _service_state(name: str) -> str:
    out = _run(["systemctl", "show", name, "-p", "ActiveState", "-p", "ActiveEnterTimestamp"])
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    state = props.get("ActiveState", "unknown")
    since = props.get("ActiveEnterTimestamp", "")
    return f"{state} since {since}" if since else state


def main() -> None:
    services = {name: _service_state(name) for name in SERVICE_NAMES}
    synced = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) or "unknown"
    free = shutil.disk_usage(CAPTURES if CAPTURES.exists() else Path.home()).free
    for line in report(CAPTURES, datetime.now(timezone.utc), services, free, synced):
        print(line)


if __name__ == "__main__":
    main()
