"""Record GivTCP's MQTT output as NDJSON snapshots that join_streams.py reads.

GivTCP publishes one value per MQTT message. This logger keeps the latest
value of every topic and, at most once a second when something changed,
writes one record holding all of them:

    {"ts": "<UTC ISO 8601>", "fields": {...}}

Full snapshots matter: join_streams takes the last TCP record before each
wire frame, so a record with one field would blank every other column. For
the same reason, after a restart the logger holds its first snapshot until
it has every field of the last record on disk, or for at most WARMUP_S.

If no message arrives for MQTT_STALL_S seconds (default 600), e.g. because
paho's network thread has died, the logger exits with status 1 so systemd
restarts it.

Configuration comes from environment variables (see capture-box/mqtt.env.example):
    MQTT_HOST, MQTT_PORT (default 1883), MQTT_USER, MQTT_PASSWORD,
    MQTT_TOPIC_PREFIX, MQTT_STALL_S (default 600),
    GIVCAP_TCP_PATH (strftime template, UTC date).

Run: python tools/mqtt_logger.py   (needs paho-mqtt; pip install '.[capture]')
"""
import json
import math
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

# GivTCP topic path after the prefix -> tcp_poller.py field name, so the
# notebook and join_streams.py see the same column names as a poller capture.
# Filled in from a recording of the real broker (capture-box/README.md).
TOPIC_TO_FIELD: dict[str, str] = {}

DEFAULT_PATH = "~/captures/%Y-%m-%d/tcp.ndjson"
REQUIRED_ENV = ("MQTT_HOST", "MQTT_USER", "MQTT_PASSWORD", "MQTT_TOPIC_PREFIX")
WARMUP_S = 120.0


def field_name(topic: str, prefix: str) -> str:
    """Column name for a topic: the poller's name if mapped, else the path joined with underscores.

    Case is kept, because topics can contain serial numbers and redact.py
    matches serials case-sensitively.
    """
    rest = topic[len(prefix):].lstrip("/") if topic.startswith(prefix + "/") else topic
    if rest in TOPIC_TO_FIELD:
        return TOPIC_TO_FIELD[rest]
    return re.sub(r"[^0-9A-Za-z]+", "_", rest).strip("_")


def parse_payload(payload: bytes):
    """int or finite float for a number, None for empty or non-finite, else the stripped text.

    None (JSON null) rather than text for nan/inf/empty keeps numeric topics
    numeric, so join_streams can write them to parquet.
    """
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = float(text)
    except ValueError:
        return text
    return value if math.isfinite(value) else None


def last_field_names(path: Path) -> set:
    """Field names of the last complete record in an NDJSON file, or an empty set."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return set()
    for line in reversed(lines):
        try:
            return set(json.loads(line)["fields"])
        except (ValueError, KeyError, TypeError):
            continue                      # a line cut off by a power loss
    return set()


def expected_fields(path_template: str, now: datetime) -> set:
    """Fields the first snapshot should wait for: today's last record, else yesterday's."""
    for day in (now, now - timedelta(days=1)):
        names = last_field_names(Path(day.strftime(path_template)))
        if names:
            return names
    return set()


class Watchdog:
    """Says when no message has arrived for stall_s seconds. Times are time.monotonic() values."""

    def __init__(self, stall_s: float, now: float):
        self.stall_s = stall_s
        self._last = now

    def feed(self, now: float) -> None:
        self._last = now

    def stalled(self, now: float) -> bool:
        return now - self._last >= self.stall_s


class SnapshotWriter:
    """Holds the latest value per field and appends full snapshots to a daily file.

    The first snapshot waits until every name in expected_fields has a value, or until
    warmup_s after the first chance to write, whichever comes first.
    """

    def __init__(self, path_template: str, min_interval_s: float = 1.0,
                 expected_fields: set = frozenset(), warmup_s: float = WARMUP_S):
        self.path_template = path_template
        self.min_interval_s = min_interval_s
        self.expected_fields = set(expected_fields)
        self.warmup_s = warmup_s
        self._fields: dict = {}
        self._dirty = False
        self._last_write: datetime | None = None
        self._warm_since: datetime | None = None
        self._lock = threading.Lock()

    def update(self, name: str, value) -> None:
        with self._lock:
            if name not in self._fields or self._fields[name] != value:
                self._fields[name] = value
                self._dirty = True

    def maybe_write(self, now: datetime) -> bool:
        """Write a snapshot if something changed and min_interval_s has passed. Returns True if written."""
        if now.tzinfo is None:
            raise ValueError("maybe_write needs a timezone-aware UTC datetime")
        now = now.astimezone(timezone.utc)
        with self._lock:
            if not self._dirty:
                return False
            if self._last_write is None:
                if self._warm_since is None:
                    self._warm_since = now
                waited = (now - self._warm_since).total_seconds()
                if not self.expected_fields <= self._fields.keys() and waited < self.warmup_s:
                    return False
            elif (now - self._last_write).total_seconds() < self.min_interval_s:
                return False
            record = {"ts": now.isoformat(), "fields": dict(self._fields)}
            self._dirty = False
            self._last_write = now
        path = Path(now.strftime(self.path_template))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, allow_nan=False) + "\n")
        return True


def load_config(env: Mapping[str, str]) -> dict:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise SystemExit(f"mqtt_logger: missing environment variables: {', '.join(missing)}")
    return {
        "host": env["MQTT_HOST"],
        "port": int(env.get("MQTT_PORT", "1883")),
        "user": env["MQTT_USER"],
        "password": env["MQTT_PASSWORD"],
        "prefix": env["MQTT_TOPIC_PREFIX"].rstrip("/"),
        "path": env.get("GIVCAP_TCP_PATH", DEFAULT_PATH),
        "stall_s": int(env.get("MQTT_STALL_S", "600")),
    }


def main() -> None:
    import paho.mqtt.client as mqtt

    cfg = load_config(os.environ)
    path = os.path.expanduser(cfg["path"])
    writer = SnapshotWriter(path, expected_fields=expected_fields(path, datetime.now(timezone.utc)))
    watchdog = Watchdog(cfg["stall_s"], time.monotonic())
    prefix = cfg["prefix"]

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"mqtt_logger: connect failed: {reason_code}", file=sys.stderr)
            return
        client.subscribe(f"{prefix}/#")
        print(f"mqtt_logger: subscribed to {prefix}/#", file=sys.stderr)

    def on_message(client, userdata, msg):
        watchdog.feed(time.monotonic())
        writer.update(field_name(msg.topic, prefix), parse_payload(msg.payload))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="givcap-mqtt-logger")
    client.username_pw_set(cfg["user"], cfg["password"])
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(cfg["host"], cfg["port"])
    client.loop_start()
    try:
        while not watchdog.stalled(time.monotonic()):
            writer.maybe_write(datetime.now(timezone.utc))
            time.sleep(0.2)
    except KeyboardInterrupt:
        return
    finally:
        client.loop_stop()
    print(f"mqtt_logger: no message for {cfg['stall_s']} s, exiting so systemd restarts me", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
