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


# --- Real implementation ---

# Field names come from the library's actual model attributes.
# Inverter fields: plant.inverter.<name>
#   status, v_battery (centi-V), p_battery (W, signed), temp_battery (deci-degC)
#   battery_charge_limit, battery_discharge_limit (HR 111/112, % of rated power)
# Battery[0] fields: plant.batteries[0].<name>
#   soc, v_cells_sum (milli-V), num_cycles
#   t_max, t_min (deci-degC)
#   v_cell_01..v_cell_16 (milli-V, per cell)
#   warning_1, warning_2, status_1..status_7
#   cap_remaining, cap_calibrated (centi-Ah)
# Note: the example REGISTER_FIELDS list used names like "battery_soc" which
# do not exist in this library. The corrected names are used below.
REGISTER_FIELDS = [
    # Inverter-side -- from plant.inverter
    "status",
    "battery_charge_limit",
    "battery_discharge_limit",
    "v_battery",
    "p_battery",
    "temp_battery",
    # Battery[0]-side -- from plant.batteries[0]
    "soc",
    "v_cells_sum",
    "t_max",
    "t_min",
    "num_cycles",
    "warning_1",
    "warning_2",
    "cap_remaining",
    "cap_calibrated",
    # Per-cell voltages (battery[0])
    "v_cell_01",
    "v_cell_02",
    "v_cell_03",
    "v_cell_04",
    "v_cell_05",
    "v_cell_06",
    "v_cell_07",
    "v_cell_08",
    "v_cell_09",
    "v_cell_10",
    "v_cell_11",
    "v_cell_12",
    "v_cell_13",
    "v_cell_14",
    "v_cell_15",
    "v_cell_16",
]


class GivEnergyPollSource:
    """Real PollSource backed by givenergy-modbus-async.

    Package is 'givenergy_modbus' (not 'givenergy_modbus_async').
    Client lives at givenergy_modbus.client.client.Client.
    Refresh is client.refresh_plant(full_refresh=False).
    Plant exposes .inverter (Inverter) and .batteries (list[Battery]).
    There is no detect_plant(); use refresh_plant(full_refresh=True) on first call
    to populate number_batteries so plant.batteries is non-empty.
    """

    def __init__(self, host: str, port: int = 8899):
        self.host = host
        self.port = port
        self._client = None

    async def connect(self):
        from givenergy_modbus.client.client import Client  # type: ignore
        self._client = Client(host=self.host, port=self.port)
        await self._client.connect()
        # Full refresh on connect so number_batteries is detected.
        await self._client.refresh_plant(full_refresh=True)

    async def fetch(self) -> dict:
        assert self._client is not None, "call connect() first"
        await self._client.refresh_plant(full_refresh=False)
        plant = self._client.plant
        record = {}
        for name in REGISTER_FIELDS:
            record[name] = _read_attr(plant, name)
        return record

    async def close(self):
        if self._client is not None:
            await self._client.close()


def _read_attr(plant, name: str):
    """Look up `name` on the plant model.

    Tries plant.inverter first, then plant.batteries[0], then plant itself.
    Returns None on any miss.
    """
    for root in (
        getattr(plant, "inverter", None),
        *getattr(plant, "batteries", []),
        plant,
    ):
        if root is None:
            continue
        try:
            val = root.get(name)
            if val is not None:
                return val
        except Exception:
            pass
        if hasattr(root, name):
            return getattr(root, name)
    return None


# --- CLI ---

async def _run_cli(host: str, port: int, out: Path, interval: float):
    src = GivEnergyPollSource(host, port)
    await src.connect()
    try:
        await poll_loop(src, out, interval=interval)
    finally:
        await src.close()


def main():
    import argparse
    p = argparse.ArgumentParser(description="Modbus TCP poller for GivEnergy inverter")
    p.add_argument("--host", required=True, help="Inverter local IP")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--out", type=Path, required=True, help="Output NDJSON path")
    p.add_argument("--interval", type=float, default=1.0, help="Poll interval (seconds)")
    args = p.parse_args()
    try:
        asyncio.run(_run_cli(args.host, args.port, args.out, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
