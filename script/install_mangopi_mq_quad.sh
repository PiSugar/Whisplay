#!/usr/bin/env bash
# Install Whisplay on MangoPi MQ Quad (Allwinner H616).

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOUNDCARD_DIR="${WHISPLAY_SOUNDCARD_DIR:-$PROJECT_ROOT/audio/whisplay-soundcard}"

log()  { echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }
die()  { echo "[X] $*" >&2; exit 1; }

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "This script must be run as root (use sudo)."
}

is_mangopi_mq_quad() {
  local model="" compat="" sdio_device=""
  [[ -r /proc/device-tree/model ]] && model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
  [[ -r /proc/device-tree/compatible ]] && compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"
  if [[ "${model^^}" == *"MQ-QUAD"* ]] || \
      echo "$compat" | grep -qi 'mangopi.*mq.*quad\|widora.*mq.*quad'; then
    return 0
  fi
  for sdio_device in /sys/bus/sdio/devices/*/device; do
    if [[ -r "$sdio_device" ]] && grep -qi '^0xd723$' "$sdio_device"; then
      return 0
    fi
  done
  return 1
}

install_platform_deps() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-dev python3-pip python3-libgpiod python3-spidev \
    python3-pil python3-pygame i2c-tools device-tree-compiler alsa-utils \
    libasound2-plugins sox make gcc wget xz-utils kmod
}

detect_interfaces() {
  local class_path="" i2c_dev="" lcd_spidev="" of_node="" scan=""
  echo
  for class_path in /sys/class/i2c-adapter/i2c-*; do
    [[ -e "$class_path" ]] || continue
    of_node="$(readlink -f "$class_path/device/of_node" 2>/dev/null || true)"
    if [[ "$of_node" == */i2c@5002400 ]]; then
      i2c_dev="/dev/${class_path##*/}"
      break
    fi
  done
  if [[ -n "$i2c_dev" && -e "$i2c_dev" ]] && command -v i2cdetect >/dev/null 2>&1; then
    scan="$(i2cdetect -y "${i2c_dev##*-}" 2>/dev/null || true)"
    if grep -Eq '(^|[[:space:]])(10|1a|UU)([[:space:]]|$)' <<<"$scan"; then
      ok "Whisplay codec detected on $i2c_dev"
    else
      warn "No Whisplay codec found on $i2c_dev; a reboot may still be required"
    fi
  else
    warn "40-pin I2C1 controller is not available yet; reboot is required"
  fi
  for class_path in /sys/class/spidev/spidev*.*; do
    [[ -e "$class_path" ]] || continue
    of_node="$(readlink -f "$class_path/device/of_node" 2>/dev/null || true)"
    if [[ "$of_node" == */spi@5011000/spidev@0 ]]; then
      lcd_spidev="/dev/${class_path##*/}"
      break
    fi
  done
  [[ -n "$lcd_spidev" && -e "$lcd_spidev" ]] && \
    ok "LCD SPI1 CS0 available at $lcd_spidev" || \
    warn "LCD SPI1 CS0 is not available yet; reboot is required"
}

need_root
is_mangopi_mq_quad || die "This installer only supports MangoPi MQ Quad."
[[ -f "$SOUNDCARD_DIR/scripts/install.sh" ]] || die "Missing unified sound card installer: $SOUNDCARD_DIR/scripts/install.sh"

echo "============================================="
echo " Whisplay HAT Driver Install - MangoPi MQ Quad"
echo "============================================="
echo "Detected platform: $(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
echo "Kernel: $(uname -r)"
echo

log "Installing MangoPi platform dependencies..."
install_platform_deps

log "Installing LCD SPI1, I2C1/I2S0, and unified sound-card overlay..."
WHISPLAY_PLATFORM=mangopi_mq_quad bash "$SOUNDCARD_DIR/scripts/install.sh"

detect_interfaces

echo
echo "Installation complete. Reboot, then verify with:"
echo "  aplay -l | grep -i whisplay"
echo "  amixer -c whisplaysound cget name='speaker'"
echo "  amixer -c whisplaysound cget name='mic'"
echo "  cd $PROJECT_ROOT/example && sudo bash run_test.sh"
