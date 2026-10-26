#!/usr/bin/env bash
#
# One-shot setup for the USB wipe station on a NanoPi NEO running DietPi.
#
# Usage (from inside the cloned repo):
#   sudo ./install.sh leds       # version with the 4 status LEDs
#   sudo ./install.sh no-leds    # OLED only
#
# Safe to re-run: every step checks before changing anything.

set -euo pipefail

case "${1:-}" in
    leds)    SCRIPT="usb_display_with_leds.py" ;;
    no-leds) SCRIPT="usb_display_no_leds.py" ;;
    *)
        echo "Usage: sudo ./install.sh [leds|no-leds]"
        exit 1
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo ./install.sh ${1}"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="/etc/systemd/system/usb-display.service"
ENV_FILE="/boot/dietpiEnv.txt"
NEED_REBOOT=0

echo "==> Installing system packages"
apt-get update
apt-get install -y \
    i2c-tools python3-pip python3-dev python3-smbus \
    libjpeg-dev zlib1g-dev libfreetype6-dev \
    libtiff6 libopenjp2-7 liblcms2-2 libxcb1 \
    dosfstools

echo "==> Installing Python packages"
pip3 install -r "${REPO_DIR}/requirements.txt" --break-system-packages

echo "==> Checking I2C overlay in ${ENV_FILE}"
if [ ! -f "${ENV_FILE}" ]; then
    echo "    ${ENV_FILE} not found - enable I2C0 manually for this OS image."
elif grep -qE '^overlays=(.* )?i2c0( .*)?$' "${ENV_FILE}"; then
    echo "    i2c0 overlay already enabled"
elif grep -q '^overlays=' "${ENV_FILE}"; then
    sed -i '/^overlays=/ s/$/ i2c0/' "${ENV_FILE}"
    echo "    added i2c0 to existing overlays line"
    NEED_REBOOT=1
else
    echo "overlays=i2c0" >> "${ENV_FILE}"
    echo "    added new overlays line"
    NEED_REBOOT=1
fi

echo "==> Installing systemd service (${SCRIPT})"
sed -e "s|__REPO_DIR__|${REPO_DIR}|g" \
    -e "s|__SCRIPT__|${SCRIPT}|g" \
    "${REPO_DIR}/usb-display.service" > "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable usb-display.service

if [ "${NEED_REBOOT}" -eq 1 ]; then
    echo
    echo "Done. The I2C overlay was just added, so reboot to activate it:"
    echo "    sudo reboot"
else
    systemctl restart usb-display.service
    echo
    echo "Done. Service started. Watch it with:"
    echo "    sudo journalctl -u usb-display.service -f"
fi
