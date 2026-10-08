#!/usr/bin/env bash
#
# Kiln controller installer for Raspberry Pi OS (Trixie or Bookworm),
# including the original Pi Zero W.
#
# Normal use, on the pi as your user (not root):
#
#   curl -sSL https://raw.githubusercontent.com/adman234/kiln-controller/main/install.sh | bash
#   # or from a clone:  ./install.sh
#
# Options:
#   --protect-sd     reduce SD card writes: logs in RAM, /tmp in RAM, no swap
#                    file, noatime, no daily apt jobs (recommended)
#   --tailscale      install Tailscale for safe remote access (run
#                    "sudo tailscale up" afterwards, or set TAILSCALE_AUTHKEY)
#   --settings FILE  copy a settings JSON (e.g. kiln-settings.json) into
#                    storage/settings.json
#   --no-start       install and enable the service but do not start it
#   --branch NAME    git branch to install (default main)
#
# It can also run as root for unattended installs (see provision/vendor-data):
#   KILN_USER=<user> ./install.sh ...
#
# Safe to run again to update: it pulls the latest code, updates packages
# and restarts the service. Settings and schedules in storage/ are kept.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/adman234/kiln-controller}"
BRANCH="${BRANCH:-main}"
PROTECT_SD="${PROTECT_SD:-0}"
TAILSCALE="${TAILSCALE:-0}"
START=1
SETTINGS_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --protect-sd) PROTECT_SD=1 ;;
        --tailscale) TAILSCALE=1 ;;
        --no-start) START=0 ;;
        --settings) SETTINGS_FILE="$2"; shift ;;
        --branch) BRANCH="$2"; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 1 ;;
    esac
    shift
done

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mwarning: %s\033[0m\n' "$*"; }

# who runs the controller, and how we get root
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    KILN_USER="${KILN_USER:-${SUDO_USER:-}}"
    if [ -z "${KILN_USER}" ]; then
        KILN_USER="$(getent passwd 1000 | cut -d: -f1 || true)"
    fi
    if [ -z "${KILN_USER}" ] || [ "${KILN_USER}" = "root" ]; then
        echo "Running as root: set KILN_USER to the normal user that should run the controller." >&2
        exit 1
    fi
    as_user() { runuser -u "${KILN_USER}" -- "$@"; }
else
    SUDO="sudo"
    KILN_USER="$(id -un)"
    as_user() { "$@"; }
fi
USER_HOME="$(getent passwd "${KILN_USER}" | cut -d: -f6)"

# where are we installing?
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/kiln-controller.py" ]; then
    INSTALL_DIR="${SCRIPT_DIR}"
else
    INSTALL_DIR="${INSTALL_DIR:-${USER_HOME}/kiln-controller}"
fi

ARCH="$(uname -m)"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
say "Installing kiln-controller in ${INSTALL_DIR} for user ${KILN_USER} on ${MODEL} (${ARCH})"
if [ "${ARCH}" = "armv6l" ]; then
    echo "Pi Zero / Pi 1 detected. This takes 15-40 minutes, mostly apt and pip."
fi

say "Installing system packages"
${SUDO} apt-get update -y
# gevent comes from apt: compiling it on a pi zero takes hours
${SUDO} env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3 python3-venv python3-dev python3-pip python3-gevent \
    build-essential i2c-tools

say "Getting the code"
if [ -d "${INSTALL_DIR}/.git" ]; then
    as_user git -C "${INSTALL_DIR}" pull --ff-only || warn "could not fast-forward ${INSTALL_DIR}, keeping current code"
else
    as_user git clone --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

say "Enabling SPI and I2C"
if command -v raspi-config >/dev/null 2>&1; then
    ${SUDO} raspi-config nonint do_spi 0
    ${SUDO} raspi-config nonint do_i2c 0
else
    warn "raspi-config not found, enable SPI (and I2C for MCP9600) yourself"
fi
for g in gpio spi i2c video; do
    if getent group "$g" >/dev/null; then
        ${SUDO} usermod -aG "$g" "${KILN_USER}"
    fi
done

say "Creating Python virtual environment"
if [ ! -x venv/bin/python ]; then
    # system site packages so the apt gevent is used
    as_user python3 -m venv --system-site-packages venv
fi
as_user venv/bin/pip install --upgrade pip wheel
# piwheels (preconfigured on Raspberry Pi OS) provides prebuilt ARM wheels
as_user venv/bin/pip install -r requirements.txt

as_user mkdir -p storage/profiles
if [ -n "${SETTINGS_FILE}" ]; then
    say "Applying settings from ${SETTINGS_FILE}"
    as_user venv/bin/python tools/merge-settings.py "${SETTINGS_FILE}" storage/settings.json
fi

say "Installing the systemd service"
sed -e "s|@USER@|${KILN_USER}|g" -e "s|@DIR@|${INSTALL_DIR}|g" lib/init/kiln-controller.service \
    | ${SUDO} tee /etc/systemd/system/kiln-controller.service > /dev/null

# hardware watchdog: if the whole pi locks up it reboots, and the relay
# goes off with it
${SUDO} mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\nRebootWatchdogSec=2min\n' \
    | ${SUDO} tee /etc/systemd/system.conf.d/kiln-watchdog.conf > /dev/null

# keep logs from wearing out the SD card
${SUDO} mkdir -p /etc/systemd/journald.conf.d
if [ "${PROTECT_SD}" = "1" ]; then
    printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=30M\n' | ${SUDO} tee /etc/systemd/journald.conf.d/kiln-controller.conf > /dev/null
else
    printf '[Journal]\nSystemMaxUse=50M\n' | ${SUDO} tee /etc/systemd/journald.conf.d/kiln-controller.conf > /dev/null
fi

# wifi power saving makes the web UI on a pi zero very sluggish
if [ -d /etc/NetworkManager/conf.d ]; then
    printf '[connection]\nwifi.powersave = 2\n' | ${SUDO} tee /etc/NetworkManager/conf.d/kiln-wifi-powersave.conf > /dev/null
fi

if [ "${PROTECT_SD}" = "1" ]; then
    say "Reducing SD card writes"
    # temp files in RAM
    if ! grep -q "^tmpfs[[:space:]]\+/tmp[[:space:]]" /etc/fstab; then
        echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,size=64m 0 0" | ${SUDO} tee -a /etc/fstab > /dev/null
    fi
    # don't write access times on every file read
    ${SUDO} sed -i -E '/[[:space:]]\/[[:space:]]+ext4/ { /noatime/! s/(ext4[[:space:]]+)([^[:space:]]+)/\1\2,noatime/ }' /etc/fstab
    # swap file on the SD card (Bookworm); Trixie swaps to compressed RAM
    if systemctl list-unit-files dphys-swapfile.service >/dev/null 2>&1; then
        ${SUDO} dphys-swapfile swapoff 2>/dev/null || true
        ${SUDO} systemctl disable --now dphys-swapfile 2>/dev/null || true
    fi
    # nightly package list downloads and man page indexing
    for t in apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
        ${SUDO} systemctl disable --now "$t" 2>/dev/null || true
    done
fi

if [ "${TAILSCALE}" = "1" ]; then
    say "Installing Tailscale for remote access"
    if ! command -v tailscale >/dev/null 2>&1; then
        curl -fsSL https://tailscale.com/install.sh | ${SUDO} sh
    fi
    if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
        ${SUDO} tailscale up --authkey "${TAILSCALE_AUTHKEY}" --hostname "$(hostname)" || warn "tailscale up failed"
    else
        echo "Run 'sudo tailscale up' to log this kiln into your tailnet."
    fi
fi

${SUDO} systemctl daemon-reload
[ "${START}" = "1" ] && { ${SUDO} systemctl daemon-reexec || true; }
${SUDO} systemctl restart systemd-journald || true
${SUDO} systemctl enable kiln-controller
if [ "${START}" = "1" ]; then
    ${SUDO} systemctl restart kiln-controller
fi

HOST="$(hostname)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done!"
cat <<EOF

  Open  http://${HOST}.local:8081   (or http://${IP}:8081)

  The controller starts in SIMULATION mode unless your settings say
  otherwise, so nothing heats up by accident. When your sensor and relay
  are wired:
    1. Settings -> Sensor / Hardware: pick your board, thermocouple type and pins
    2. Settings -> Advanced: untick "Simulation mode", Save, Restart controller
    3. Settings -> Diagnostics: check the temperature and test the relay
    4. Settings -> Safety & Alerts: set up phone alerts
    5. Settings -> PID & Autotune: run an autotune with the kiln empty

  Logs:     journalctl -u kiln-controller -f
  Restart:  sudo systemctl restart kiln-controller
  Update:   ${INSTALL_DIR}/install.sh

EOF
