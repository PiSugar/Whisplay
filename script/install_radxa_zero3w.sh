#!/usr/bin/env bash
#
# Whisplay HAT Driver Install Script - Radxa ZERO 3W
#

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOUNDCARD_DIR="${WHISPLAY_SOUNDCARD_DIR:-$PROJECT_ROOT/audio/whisplay-soundcard}"
DTBO_DIR="/boot/dtbo"

log()  { echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }
die()  { echo "[X] $*" >&2; exit 1; }

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "This script must be run as root (use sudo)."
}

is_radxa_zero3w() {
  local model=""
  local compat=""

  [[ -r /proc/device-tree/model ]] && model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
  [[ -r /proc/device-tree/compatible ]] && compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"

  [[ "$model" == *"Cubie"* ]] || echo "$compat" | grep -qi "cubie-a7z" && return 1
  [[ "$model" == *"Radxa"* ]] || echo "$compat" | grep -qi "radxa"
}

install_platform_deps() {
  log "Installing Radxa platform dependencies..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    python3-libgpiod \
    python3-spidev \
    python3-pil \
    python3-pygame \
    i2c-tools \
    device-tree-compiler \
    alsa-utils \
    libasound2-plugins \
    sox \
    make \
    gcc
}

enable_overlay_copy() {
  local overlay="$1"
  local label="$2"

  if [[ -f "${DTBO_DIR}/${overlay}" ]]; then
    ok "$label already enabled"
  elif [[ -f "${DTBO_DIR}/${overlay}.disabled" ]]; then
    cp "${DTBO_DIR}/${overlay}.disabled" "${DTBO_DIR}/${overlay}"
    ok "$label enabled: $overlay"
  else
    warn "$label overlay not found; enable it manually with rsetup if needed: $overlay"
  fi
}

detect_interfaces() {
  echo
  log "Detecting hardware interfaces..."
  if command -v i2cdetect >/dev/null 2>&1; then
    for bus in 0 3 6; do
      if [[ -e "/dev/i2c-${bus}" ]] && i2cdetect -y "$bus" 2>/dev/null | grep -q " 1a "; then
        ok "WM8960 detected on I2C bus $bus (address 0x1a)"
      fi
    done
  fi

  if [[ -e /dev/spidev3.0 ]]; then
    ok "SPI3 device available (/dev/spidev3.0)"
  else
    warn "SPI3 device not found yet; reboot may be required"
  fi
}

need_root
is_radxa_zero3w || die "This installer only supports Radxa ZERO 3W. For Raspberry Pi, use script/install_raspberry_pi.sh."

[[ -f "$SOUNDCARD_DIR/scripts/install.sh" ]] || \
  die "Missing unified sound card installer: $SOUNDCARD_DIR/scripts/install.sh"

echo "=============================================="
echo " Whisplay HAT Driver Install - Radxa ZERO 3W"
echo "=============================================="
echo
echo "Detected platform: $(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
echo "Sound driver: $SOUNDCARD_DIR"
echo

install_platform_deps

echo
log "Enabling SPI3 overlay for LCD display..."
enable_overlay_copy "rk3568-spi3-m1-cs0-spidev.dtbo" "SPI3"

echo
log "Enabling I2C3-M0 overlay for the 40-pin header..."
enable_overlay_copy "rk3568-i2c3-m0.dtbo" "I2C3-M0"

echo
log "Installing unified Whisplay sound card driver..."
WHISPLAY_PLATFORM=radxa_zero3w bash "$SOUNDCARD_DIR/scripts/install.sh"

detect_interfaces

echo
echo "=============================================="
echo " Installation complete."
echo "=============================================="
echo
echo "Reboot to apply all changes:"
echo "  sudo reboot"
echo
echo "After reboot, verify:"
echo "  aplay -l | grep -i whisplay"
echo "  amixer -c whisplaysound cget name='speaker'"
echo "  amixer -c whisplaysound cget name='mic'"
echo
echo "Run the hardware test:"
echo "  cd $PROJECT_ROOT/example"
echo "  sudo bash run_test.sh"
