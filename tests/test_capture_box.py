"""Static checks on the capture-box files: units, udev rule, tokens, shell syntax."""
import configparser
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BOX = REPO / "capture-box"
UNITS = ["givcap-wire.service", "givcap-mqtt.service", "givcap-compress.service", "givcap-compress.timer"]
TEMPLATED = UNITS + ["99-rs485.rules", "givcap-status"]
SETUP_TOKENS = {"@USER@", "@HOME@", "@REPO@", "@VENDOR@", "@PRODUCT@", "@SERIAL@"}


def _unit(name):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string((BOX / name).read_text())
    return cp


@pytest.mark.parametrize("name", ["givcap-wire.service", "givcap-mqtt.service"])
def test_logger_units_wait_for_clock_and_restart(name):
    u = _unit(name)
    assert "time-sync.target" in u["Unit"]["After"]
    assert "time-sync.target" in u["Unit"]["Wants"]
    assert u["Service"]["Restart"] == "always"
    assert u["Service"]["RestartSec"] == "10"
    assert u["Service"]["User"] == "@USER@"
    assert u["Install"]["WantedBy"] == "multi-user.target"


def test_wire_unit_is_bound_to_dongle_and_listens_only():
    u = _unit("givcap-wire.service")
    assert u["Unit"]["BindsTo"] == "dev-rs485.device"
    assert "dev-rs485.device" in u["Unit"]["After"]
    assert u["Service"]["ExecStart"].startswith("/usr/local/bin/serial_hexdump_logger /dev/rs485 ")


def test_mqtt_unit_reads_secret_env_file():
    u = _unit("givcap-mqtt.service")
    assert u["Service"]["EnvironmentFile"] == "/etc/givcap/mqtt.env"
    assert "@REPO@/tools/mqtt_logger.py" in u["Service"]["ExecStart"]


def test_unit_date_templates_escape_percent():
    for name in UNITS:
        text = (BOX / name).read_text()
        for match in re.finditer(r"%+[YmdHMS]", text):
            assert match.group().startswith("%%"), f"{name}: unescaped {match.group()!r}"
    assert "%%Y-%%m-%%d/wire.log" in (BOX / "givcap-wire.service").read_text()
    assert "%%Y-%%m-%%d/tcp.ndjson" in (BOX / "givcap-mqtt.service").read_text()


def test_timer_runs_daily_after_midnight_utc():
    u = _unit("givcap-compress.timer")
    assert u["Timer"]["OnCalendar"] == "*-*-* 00:30:00 UTC"
    assert u["Timer"]["Persistent"] == "true"


def test_udev_rule_names_dongle_and_starts_wire_logger():
    rule = (BOX / "99-rs485.rules").read_text()
    active = [l for l in rule.splitlines() if l.strip() and not l.startswith("#")]
    assert len(active) == 1
    line = active[0]
    for part in ('SUBSYSTEM=="tty"', 'ATTRS{idVendor}=="@VENDOR@"', 'ATTRS{idProduct}=="@PRODUCT@"',
                 'ATTRS{serial}=="@SERIAL@"', 'SYMLINK+="rs485"', 'TAG+="systemd"',
                 'ENV{SYSTEMD_WANTS}+="givcap-wire.service"'):
        assert part in line


def test_every_token_is_rendered_by_setup():
    setup = (BOX / "setup.sh").read_text()
    for name in TEMPLATED:
        for token in set(re.findall(r"@[A-Z]+@", (BOX / name).read_text())):
            assert token in SETUP_TOKENS, f"{name}: unknown token {token}"
    for token in SETUP_TOKENS:
        assert f"s|{token}|" in setup, f"setup.sh does not render {token}"


def test_units_point_at_files_in_repo():
    for name in UNITS + ["givcap-status"]:
        for rel in re.findall(r"@REPO@/([\w./-]+)", (BOX / name).read_text()):
            if rel.startswith(".venv/"):
                continue
            assert (REPO / rel).exists(), f"{name}: {rel} not in repo"


@pytest.mark.parametrize("name", ["setup.sh", "givcap-compress", "givcap-status"])
def test_shell_scripts_parse(name):
    subprocess.run(["bash", "-n", str(BOX / name)], check=True)


def test_mqtt_env_example_has_every_required_variable():
    from tools.mqtt_logger import REQUIRED_ENV
    text = (BOX / "mqtt.env.example").read_text()
    for name in REQUIRED_ENV:
        assert re.search(rf"^{name}=", text, re.M), name


def test_wire_unit_can_open_the_serial_device():
    assert _unit("givcap-wire.service")["Service"]["SupplementaryGroups"] == "dialout"


def test_setup_does_not_wait_on_an_absent_dongle():
    setup = (BOX / "setup.sh").read_text()
    assert "[[ -e /dev/rs485 ]]" in setup
    assert "systemctl --no-block restart givcap-wire.service" in setup
