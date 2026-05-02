"""Unit tests for tcp_poller — exercise the poll loop with a fake source."""
import asyncio
import json
from pathlib import Path

import pytest

from tools.tcp_poller import PollSource, poll_loop


class FakeSource(PollSource):
    def __init__(self, scripted_records):
        self.scripted = list(scripted_records)
        self.calls = 0

    async def fetch(self):
        if not self.scripted:
            raise StopAsyncIteration
        self.calls += 1
        return self.scripted.pop(0)


@pytest.mark.asyncio
async def test_poll_loop_writes_one_ndjson_record_per_fetch(tmp_path):
    out = tmp_path / "tcp.ndjson"
    src = FakeSource([
        {"battery_soc": 50, "inverter_mode": "idle"},
        {"battery_soc": 51, "inverter_mode": "charge"},
    ])

    await poll_loop(src, out, interval=0.0, max_iterations=2)

    lines = out.read_text().strip().split("\n")
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["fields"]["battery_soc"] == 50
    assert "ts" in rec0
    rec1 = json.loads(lines[1])
    assert rec1["fields"]["inverter_mode"] == "charge"
