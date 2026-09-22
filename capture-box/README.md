# Capture box

A Raspberry Pi that sits next to a GivEnergy inverter and records, around the clock:

- every byte on the inverter's BMS RS485 bus, via a USB RS485 dongle wired as a passive tap (`wire.log`)
- GivTCP's view of the inverter and battery, from its MQTT output (`tcp.ndjson`)

The box only listens. It never transmits on the RS485 bus and never polls the inverter.

Captures land in `~/captures/YYYY-MM-DD/` on the Pi, one folder per UTC day. Finished days are compressed at 00:30 UTC.

## What you need

- A Raspberry Pi 3 B+ or newer, its power supply, and a 16 GB or larger microSD card (a "high endurance" card is best)
- A USB RS485 dongle, e.g. the Waveshare isolated one, tapped into the inverter's BMS terminal block (see [docs/06-wire-captures.md](../docs/06-wire-captures.md#tap-point))
- Home Assistant with the GivTCP add-on publishing to MQTT, and an MQTT user for the Pi

## Build

1. Write Raspberry Pi OS Lite (64-bit) to the card with Raspberry Pi Imager. In Imager's settings, set the hostname to `givcap`, your wifi, your username, and SSH with your public key.
2. Boot the Pi, then from your computer: `ssh givcap.local`.
3. Clone the repo: `git clone https://github.com/open-giv/bms-analysis.git && cd bms-analysis`
4. Plug in the dongle and read its IDs: `udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial'`. Use the first value of each, which belong to the dongle itself.
5. Run `sudo capture-box/setup.sh VENDOR PRODUCT SERIAL` with those three values.
6. Edit `/etc/givcap/mqtt.env` (`sudo nano /etc/givcap/mqtt.env`) with your broker address, the Pi's MQTT username and password, and GivTCP's topic prefix. Then run `sudo systemctl restart givcap-mqtt`.

## Check

- `givcap-status` shows whether both loggers are running, how old the last line of each of today's files is, free disk space, and whether the clock is synchronised.
- `journalctl -u givcap-wire -u givcap-mqtt -n 50` shows recent log messages.

## Copy captures to your computer

```
rsync -av givcap.local:captures/ ~/givenergy/captures/
```

Keep raw captures out of git. They contain your battery and inverter serial numbers. Run [tools/redact.py](../tools/redact.py) before sharing anything.

## Map GivTCP topics to poller names

`tools/mqtt_logger.py` writes unmapped topics under names made from the topic path, with each `/` or other symbol replaced by `_` and the case kept. To give the main values the same names that `tools/tcp_poller.py` uses, so that the analysis notebook works unchanged, record a sample and fill in `TOPIC_TO_FIELD`:

```
mosquitto_sub -h BROKER -u USER -P PASSWORD -t 'PREFIX/#' -v -W 120 > givtcp_sample.txt
```
