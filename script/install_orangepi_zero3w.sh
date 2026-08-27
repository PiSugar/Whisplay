#!/usr/bin/env bash
# Install Whisplay on Orange Pi Zero 3W with the official Debian 1.0.0 image.

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

is_orangepi_zero3w() {
  local release="" boot_env=""
  [[ -r /etc/orangepi-release ]] && release="$(cat /etc/orangepi-release 2>/dev/null || true)"
  [[ -r "$BOOT_ENV" ]] && boot_env="$(cat "$BOOT_ENV" 2>/dev/null || true)"
  echo "$release" | grep -qi '^BOARD=orangepizero3w$' || \
    echo "$boot_env" | grep -qi 'orangepi-zero3w\.dtb'
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

install_platform_deps() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-dev python3-pip python3-libgpiod python3-spidev \
    python3-pil python3-pygame python3-numpy i2c-tools device-tree-compiler \
    alsa-utils libasound2-plugins sox make gcc wget xz-utils kmod dpkg-dev
}

detect_interfaces() {
  local dev scan
  echo
  for dev in /dev/i2c-*; do
    [[ -e "$dev" ]] || continue
    scan="$(i2cdetect -y "${dev##*-}" 2>/dev/null || true)"
    if grep -Eq '(^|[[:space:]])(10|1a|UU)([[:space:]]|$)' <<<"$scan"; then
      ok "Whisplay codec detected on $dev"
    fi
  done
  [[ -e /dev/spidev3.0 ]] && ok "LCD SPI available at /dev/spidev3.0" || \
    warn "/dev/spidev3.0 is not available yet; reboot is required"
}

need_root
is_orangepi_zero3w || die "This installer only supports Orange Pi Zero 3W."
[[ -f "$SOUNDCARD_DIR/scripts/install.sh" ]] || die "Missing unified sound card installer: $SOUNDCARD_DIR/scripts/install.sh"

echo "================================================"
echo " Whisplay HAT Driver Install - Orange Pi Zero 3W"
echo "================================================"
echo "Kernel: $(uname -r)"
echo
warn "Orange Pi Zero 3W requires Whisplay V2 hardware."
warn "Do not use Whisplay V1: its 5 V button circuit can cut board power."
echo

log "Installing Orange Pi platform dependencies..."
install_platform_deps

log "Enabling official TWI0 and 40-pin SPI3 CS0 overlays..."
add_kernel_overlay i2c0
add_kernel_overlay spi3-cs0-cs1-spidev

log "Installing unified Whisplay sound card driver..."
WHISPLAY_PLATFORM=orangepi_zero3w bash "$SOUNDCARD_DIR/scripts/install.sh"

detect_interfaces

echo
echo "Installation complete. Reboot, then verify with:"
echo "  aplay -l | grep -i whisplay"
echo "  amixer -c whisplaysound cget name='speaker'"
echo "  amixer -c whisplaysound cget name='mic'"
echo "  cd $PROJECT_ROOT/example && sudo bash run_test.sh"
