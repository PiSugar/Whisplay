#!/usr/bin/env bash
# Install Whisplay on Orange Pi Zero 2W with the official Debian 1.0.2 image.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOUNDCARD_DIR="${WHISPLAY_SOUNDCARD_DIR:-$PROJECT_ROOT/audio/whisplay-soundcard}"
BOOT_ENV="/boot/orangepiEnv.txt"

log()  { echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }
die()  { echo "[X] $*" >&2; exit 1; }

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "This script must be run as root (use sudo)."
}

is_orangepi_zero2w() {
  local model="" compat=""
  [[ -r /proc/device-tree/model ]] && model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
  [[ -r /proc/device-tree/compatible ]] && compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"
  [[ "$model" == *"OrangePi Zero2 W"* ]] || echo "$compat" | grep -qi 'xunlong,orangepi-zero2w'
}

add_kernel_overlay() {
  local name="$1"
  [[ -f "$BOOT_ENV" ]] || die "Missing Orange Pi boot environment: $BOOT_ENV"
  if grep -q '^overlays=' "$BOOT_ENV"; then
    if ! awk -v wanted="$name" 'BEGIN { FS="[= ]" } $1 == "overlays" { for (i=2; i<=NF; i++) if ($i == wanted) found=1 } END { exit !found }' "$BOOT_ENV"; then
      sed -i "/^overlays=/ s/$/ $name/" "$BOOT_ENV"
    fi
  else
    echo "overlays=$name" >>"$BOOT_ENV"
  fi
}

remove_kernel_overlay() {
  local name="$1"
  local current=""
  local item
  local kept=()

  [[ -f "$BOOT_ENV" ]] || die "Missing Orange Pi boot environment: $BOOT_ENV"
  current="$(sed -n 's/^overlays=//p' "$BOOT_ENV" | tail -n 1)"
  [[ -n "$current" ]] || return 0
  for item in $current; do
    [[ "$item" == "$name" ]] || kept+=("$item")
  done
  sed -i "/^overlays=/c\\overlays=${kept[*]}" "$BOOT_ENV"
}

install_platform_deps() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-dev python3-pip python3-libgpiod python3-spidev \
    python3-pil python3-pygame i2c-tools device-tree-compiler alsa-utils \
    libasound2-plugins sox make gcc wget xz-utils kmod
}

detect_interfaces() {
  echo
  if [[ -e /dev/i2c-2 ]] && command -v i2cdetect >/dev/null 2>&1; then
    local scan
    scan="$(i2cdetect -y 2 2>/dev/null || true)"
    if grep -Eq '^10:[[:space:]]+(10|UU)([[:space:]]|$)' <<<"$scan"; then
      ok "ES8389 detected on I2C1 / Linux bus 2 at 0x10"
    elif awk '$1 == "10:" && ($12 == "1a" || $12 == "UU") { found=1 } END { exit !found }' <<<"$scan"; then
      ok "WM8960 detected on I2C1 / Linux bus 2 at 0x1a"
    else
      warn "No Whisplay codec found on /dev/i2c-2; a reboot may still be required"
    fi
  else
    warn "/dev/i2c-2 is not available yet; reboot is required"
  fi

  [[ -e /dev/spidev1.0 ]] && ok "LCD SPI available at /dev/spidev1.0" || \
    warn "/dev/spidev1.0 is not available yet; reboot is required"
}

need_root
is_orangepi_zero2w || die "This installer only supports Orange Pi Zero 2W."
[[ -f "$SOUNDCARD_DIR/scripts/install.sh" ]] || die "Missing unified sound card installer: $SOUNDCARD_DIR/scripts/install.sh"

echo "================================================"
echo " Whisplay HAT Driver Install - Orange Pi Zero 2W"
echo "================================================"
echo
echo "Detected platform: $(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
echo "Kernel: $(uname -r)"
echo

log "Installing Orange Pi platform dependencies..."
install_platform_deps

log "Enabling official I2C1 and 40-pin SPI1 CS0 overlays..."
add_kernel_overlay pi-i2c1
remove_kernel_overlay spi0-spidev
add_kernel_overlay spi1-cs0-spidev

log "Installing unified Whisplay sound card driver..."
WHISPLAY_PLATFORM=orangepi_zero2w bash "$SOUNDCARD_DIR/scripts/install.sh"

detect_interfaces

echo
echo "Installation complete. Reboot, then verify with:"
echo "  aplay -l | grep -i whisplay"
echo "  amixer -c whisplaysound cget name='speaker'"
echo "  amixer -c whisplaysound cget name='mic'"
echo "  cd $PROJECT_ROOT/example && sudo bash run_test.sh"
