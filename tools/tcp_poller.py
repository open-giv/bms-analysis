"""Modbus TCP poller -- captures the inverter's view of BMS state at a steady cadence.

Output: NDJSON, one record per poll, {"ts": "...", "fields": {...}}.

The PollSource interface lets the loop be unit-tested without a real Modbus client.
The real implementation GivEnergyPollSource lives below; tests use FakeSource.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class PollSource(Protocol):
    async def fetch(self) -> dict:
        """Return a flat dict of register name -> value for one poll."""
        ...


async def poll_loop(source: PollSource, out_path: Path, interval: float = 1.0,
                    max_iterations: int | None = None) -> None:
    """Repeatedly poll `source` and append NDJSON records to `out_path`.

    `interval`: seconds between polls (best-effort, not strict).
    `max_iterations`: if set, stop after this many polls (for tests). None means run forever.
    """
    out_path = Path(out_path)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        t0 = datetime.now(timezone.utc)
        try:
            fields = await source.fetch()
        except StopAsyncIteration:
            break
        record = {"ts": t0.isoformat(), "fields": fields}
        with open(out_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        iterations += 1
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        sleep_for = max(0.0, interval - elapsed)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
