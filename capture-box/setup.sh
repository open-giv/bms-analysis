#!/bin/bash
# Set up a capture box on Raspberry Pi OS Lite.
# Run from the owner's account inside the cloned repo:
#   sudo capture-box/setup.sh VENDOR PRODUCT SERIAL
# Read VENDOR, PRODUCT and SERIAL for the dongle with:
#   udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial'  (or /dev/ttyACM0)
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi
if [[ $# -ne 3 ]]; then
    echo "Usage: sudo $0 VENDOR PRODUCT SERIAL" >&2
    exit 1
fi
VENDOR="$1"
PRODUCT="$2"
SERIAL="$3"
USER_NAME="${SUDO_USER:?Run with sudo from the owner account, not as root directly}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BOX="$REPO/capture-box"

render() {
    sed -e "s|@USER@|$USER_NAME|g" -e "s|@HOME@|$HOME_DIR|g" -e "s|@REPO@|$REPO|g" \
        -e "s|@VENDOR@|$VENDOR|g" -e "s|@PRODUCT@|$PRODUCT|g" -e "s|@SERIAL@|$SERIAL|g" "$1"
}

apt-get update
# systemd-timesyncd provides systemd-time-wait-sync, which the loggers wait for so timestamps are right.
apt-get install -y gcc python3-venv rsync mosquitto-clients systemd-timesyncd

gcc -O2 -Wall -o /usr/local/bin/serial_hexdump_logger "$REPO/tools/serial_hexdump_logger.c"
sudo -u "$USER_NAME" python3 -m venv "$REPO/.venv"
sudo -u "$USER_NAME" "$REPO/.venv/bin/pip" install --quiet 'paho-mqtt>=2.0'
sudo -u "$USER_NAME" mkdir -p "$HOME_DIR/captures"

install -m 0755 "$BOX/givcap-compress" /usr/local/bin/givcap-compress
render "$BOX/givcap-status" > /usr/local/bin/givcap-status
chmod 0755 /usr/local/bin/givcap-status

render "$BOX/99-rs485.rules" > /etc/udev/rules.d/99-rs485.rules
for unit in givcap-wire.service givcap-mqtt.service givcap-compress.service givcap-compress.timer; do
    render "$BOX/$unit" > "/etc/systemd/system/$unit"
done

# Keep the journal on disk, so a hang leaves logs behind, and let the hardware watchdog reboot the
# Pi if the system stops responding for 15 s. Both were added after the first real box hung with
# nothing in the (RAM-only) journal.
install -d /var/log/journal /etc/systemd/journald.conf.d /etc/systemd/system.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/givcap.conf
printf '[Manager]\nRuntimeWatchdogSec=15s\nRebootWatchdogSec=2min\n' > /etc/systemd/system.conf.d/givcap-watchdog.conf
systemctl restart systemd-journald

install -d -m 0700 /etc/givcap
if [[ ! -f /etc/givcap/mqtt.env ]]; then
    install -m 0600 "$BOX/mqtt.env.example" /etc/givcap/mqtt.env
    echo "Edit /etc/givcap/mqtt.env with the broker details, then run: sudo systemctl restart givcap-mqtt"
fi

udevadm control --reload
udevadm trigger --subsystem-match=tty
systemctl daemon-reload
systemctl daemon-reexec
systemctl enable systemd-time-wait-sync.service
systemctl enable givcap-wire.service givcap-mqtt.service givcap-compress.timer
systemctl start givcap-compress.timer
systemctl restart givcap-mqtt.service
# The wire logger only starts when /dev/rs485 exists; udev starts it when the dongle is plugged in.
# --no-block so setup doesn't wait out the device timeout if the dongle is missing.
if [[ -e /dev/rs485 ]]; then
    systemctl --no-block restart givcap-wire.service
else
    echo "No /dev/rs485 yet: givcap-wire will start when the dongle is plugged in."
fi
echo "Done. Check with: givcap-status"
