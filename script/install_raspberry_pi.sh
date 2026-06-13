#!/usr/bin/env bash
#
# Install the Whisplay unified sound card driver on Raspberry Pi.
#
# The driver lives in the sibling whisplay-soundcard project and exposes one
# ALSA card name, `whisplaysound`, for both WM8960 and ES8389 hardware.
#
# Usage:
#   sudo bash script/install_raspberry_pi.sh
#   sudo WHISPLAY_SOUNDCARD_DIR=/path/to/whisplay-soundcard bash script/install_raspberry_pi.sh
#

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOUNDCARD_DIR="${WHISPLAY_SOUNDCARD_DIR:-$(cd "$PROJECT_ROOT/../whisplay-soundcard" 2>/dev/null && pwd || true)}"

log()  { echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }
die()  { echo "[X] $*" >&2; exit 1; }

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "This script must be run as root (use sudo)."
}

is_pi() {
  [[ -r /proc/device-tree/model ]] || return 1
  grep -q "Raspberry Pi" /proc/device-tree/model
}

enable_spi_if_possible() {
  if command -v raspi-config >/dev/null 2>&1; then
    log "Enabling SPI for Whisplay display demos..."
    raspi-config nonint do_spi 0 || warn "Could not enable SPI automatically."
  else
    warn "raspi-config not found; enable SPI manually if display demos need it."
  fi
}

power_warning() {
  local warned=0

  if command -v vcgencmd >/dev/null 2>&1; then
    local t
    t="$(vcgencmd get_throttled 2>/dev/null || true)"
    if [[ "$t" =~ throttled=0x([0-9a-fA-F]+) ]] && [[ "${BASH_REMATCH[1]}" != "0" ]]; then
      warn "Power/thermal flags since boot: $t"
      warned=1
    fi
  fi

  if dmesg 2>/dev/null | grep -qiE "under-voltage|undervoltage|brownout|throttl"; then
    warn "Kernel log contains power-related warnings since boot."
    warned=1
  fi

  [[ "$warned" -eq 0 ]] && ok "No obvious power warnings detected since boot."
}

need_root
is_pi || die "This installer only supports Raspberry Pi."

[[ -n "$SOUNDCARD_DIR" ]] || die "Could not locate whisplay-soundcard. Set WHISPLAY_SOUNDCARD_DIR=/path/to/whisplay-soundcard."
[[ -x "$SOUNDCARD_DIR/scripts/install.sh" || -f "$SOUNDCARD_DIR/scripts/install.sh" ]] || \
  die "Missing unified driver installer: $SOUNDCARD_DIR/scripts/install.sh"

echo
echo "This installer will:"
echo "  1) Enable SPI for Whisplay display demos when raspi-config is available"
echo "  2) Build and install the unified Whisplay sound card driver"
echo "  3) Configure ALSA to use the unified card name: whisplaysound"
echo
echo "Driver source: $SOUNDCARD_DIR"
echo
read -r -p "Proceed? [y/N] " ans
ans="${ans:-N}"
[[ "$ans" =~ ^[Yy]$ ]] || die "Cancelled by user."

enable_spi_if_possible

echo
log "Running unified sound card installer..."
bash "$SOUNDCARD_DIR/scripts/install.sh"

echo
echo "--------------------------------------------------------------"
echo "Reboot recommended to load the overlay and driver cleanly."
echo "  sudo reboot"
echo
echo "After reboot, verify:"
echo "  aplay -l | grep -i whisplay"
echo "  amixer -c whisplaysound cget name='speaker'"
echo "  amixer -c whisplaysound cget name='mic'"
echo "--------------------------------------------------------------"
echo
echo "In order to run the python demos, you may need:"
echo "  sudo apt install python3-pil python3-numpy python3-pygame"
echo "--------------------------------------------------------------"

power_warning
