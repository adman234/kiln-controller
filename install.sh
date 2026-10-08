#!/usr/bin/env bash
#
# Kiln controller installer for Raspberry Pi OS (Bookworm or Trixie),
# including the original Pi Zero W.
#
# Run it on the pi as your normal user (not root):
#
#   curl -sSL https://raw.githubusercontent.com/adman234/kiln-controller/main/install.sh | bash
#
# or from a clone:
#
#   ./install.sh
#
# Safe to run again to update: it pulls the latest code, updates
# packages and restarts the service. Your settings and schedules in
# storage/ are kept.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/adman234/kiln-controller}"
BRANCH="${BRANCH:-main}"
KILN_USER="${SUDO_USER:-$(id -un)}"

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mwarning: %s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ] && [ -z "${SUDO_USER:-}" ]; then
    echo "Run this as your normal user, it uses sudo where needed." >&2
    exit 1
fi

# where are we installing?
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/kiln-controller.py" ]; then
    INSTALL_DIR="${SCRIPT_DIR}"
else
    INSTALL_DIR="${INSTALL_DIR:-$HOME/kiln-controller}"
fi

ARCH="$(uname -m)"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
say "Installing kiln-controller in ${INSTALL_DIR} for user ${KILN_USER} on ${MODEL} (${ARCH})"
if [ "${ARCH}" = "armv6l" ]; then
    echo "Pi Zero / Pi 1 detected. This takes 10-30 minutes, mostly apt and pip. Grab a coffee."
fi

say "Installing system packages"
sudo apt-get update -y
# gevent comes from apt: compiling it on a pi zero takes hours
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3 python3-venv python3-dev python3-pip python3-gevent \
    build-essential i2c-tools

say "Getting the code"
if [ -d "${INSTALL_DIR}/.git" ]; then
    git -C "${INSTALL_DIR}" pull --ff-only || warn "could not fast-forward ${INSTALL_DIR}, keeping current code"
else
    git clone --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

say "Enabling SPI and I2C"
if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_spi 0
    sudo raspi-config nonint do_i2c 0
else
    warn "raspi-config not found, enable SPI (and I2C for MCP9600) yourself"
fi
for g in gpio spi i2c video; do
    if getent group "$g" >/dev/null; then
        sudo usermod -aG "$g" "${KILN_USER}"
    fi
done

say "Creating Python virtual environment"
if [ ! -x venv/bin/python ]; then
    # system site packages so the apt gevent is used
    python3 -m venv --system-site-packages venv
fi
venv/bin/pip install --upgrade pip wheel
# piwheels (preconfigured on Raspberry Pi OS) provides prebuilt ARM wheels
venv/bin/pip install -r requirements.txt

mkdir -p storage/profiles

say "Installing the systemd service"
sed -e "s|@USER@|${KILN_USER}|g" -e "s|@DIR@|${INSTALL_DIR}|g" lib/init/kiln-controller.service \
    | sudo tee /etc/systemd/system/kiln-controller.service > /dev/null

# keep logs from wearing out the SD card
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=50M\n' | sudo tee /etc/systemd/journald.conf.d/kiln-controller.conf > /dev/null

# wifi power saving makes the web UI on a pi zero very sluggish
if [ -d /etc/NetworkManager/conf.d ]; then
    printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/kiln-wifi-powersave.conf > /dev/null
fi

sudo systemctl daemon-reload
sudo systemctl restart systemd-journald || true
sudo systemctl enable kiln-controller
sudo systemctl restart kiln-controller

HOST="$(hostname)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done!"
cat <<EOF

  Open  http://${HOST}.local:8081   (or http://${IP}:8081)

  The controller starts in SIMULATION mode so nothing heats up by accident.
  When your sensor and relay are wired:
    1. Settings -> Sensor / Hardware: pick your board, thermocouple type and pins
    2. Settings -> Advanced: untick "Simulation mode", Save, Restart controller
    3. Settings -> Diagnostics: check the temperature and test the relay
    4. Settings -> PID & Autotune: run an autotune with the kiln empty

  Logs:     journalctl -u kiln-controller -f
  Restart:  sudo systemctl restart kiln-controller
  Update:   ${INSTALL_DIR}/install.sh

EOF
if [ "${ARCH}" = "armv6l" ] || [ ! -e /dev/spidev0.0 ]; then
    echo "If SPI was just enabled, reboot once: sudo reboot"
fi
