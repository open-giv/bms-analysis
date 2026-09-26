# A stand-in for Raspberry Pi OS Lite, for trying setup.sh without a Pi (see capture-box/README.md).
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv udev dbus sudo git ca-certificates python3 procps \
    && rm -rf /var/lib/apt/lists/*
# A sudo user in dialout, as Raspberry Pi Imager creates it.
RUN useradd -m -s /bin/bash -G sudo,dialout givcapuser \
    && echo 'givcapuser ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/010_pi-nopasswd
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
