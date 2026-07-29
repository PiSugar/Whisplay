#!/usr/bin/env bash
#
# Install Whisplay on Radxa Cubie A7Z (Allwinner A733).
#
# The sound path is provided by audio/whisplay-soundcard and exposes the
# same `whisplaysound` ALSA card for either WM8960 or ES8389 hardware.
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

is_radxa_cubie_a7z() {
  local model=""
  local compat=""

  [[ -r /proc/device-tree/model ]] && \
    model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
  [[ -r /proc/device-tree/compatible ]] && \
    compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"

  [[ "$model" == *"Cubie"* ]] || echo "$compat" | grep -qi "cubie-a7z"
}

install_platform_deps() {
  log "Installing Cubie A7Z platform dependencies..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    python3-dev \
    python3-pip \
    python3-libgpiod \
    python3-pil \
    python3-pygame \
    i2c-tools \
    device-tree-compiler \
    alsa-utils \
    libasound2-plugins \
    sox \
    make \
    gcc \
    wget \
    kmod

  if ! python3 -c "import spidev" >/dev/null 2>&1; then
    if apt-cache show python3-spidev >/dev/null 2>&1; then
      apt-get install -y python3-spidev
    else
      local pip_args=(install spidev)
      if python3 -m pip help install 2>/dev/null | \
          grep -q -- "--break-system-packages"; then
        pip_args=(install --break-system-packages spidev)
      fi
      python3 -m pip "${pip_args[@]}"
    fi
  fi
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
    warn "$label overlay not found; enable it manually with rsetup: $overlay"
  fi
}

detect_interfaces() {
  local detected=""

  echo
  log "Detecting hardware interfaces..."
  if [[ -e /dev/i2c-7 ]] && command -v i2cdetect >/dev/null 2>&1; then
    detected="$(i2cdetect -y 7 2>/dev/null || true)"
    if grep -Eq '^10:[[:space:]]+(10|UU)([[:space:]]|$)' <<<"$detected"; then
      ok "ES8389 detected on I2C7 at 0x10"
    elif awk '$1 == "10:" && ($12 == "1a" || $12 == "UU") {
        found = 1
      } END {
        exit !found
      }' <<<"$detected"; then
      ok "WM8960 detected on I2C7 at 0x1a"
    else
      warn "Neither ES8389 (0x10) nor WM8960 (0x1a) responded on I2C7"
    fi
  else
    warn "I2C7 is not available yet; reboot may be required"
  fi

  if [[ -e /dev/spidev1.0 ]]; then
    ok "SPI1 device available (/dev/spidev1.0)"
  else
    warn "SPI1 device not found yet; reboot may be required"
  fi
}

install_daemon_audio_dropin() {
  local dropin_dir="/etc/systemd/system/whisplay-daemon.service.d"
  local dropin_path="$dropin_dir/20-cubie-a7z-audio.conf"

  install -d -m 0755 "$dropin_dir"
  cat >"$dropin_path" <<'EOF'
[Unit]
After=sound.target whisplay-soundcard-a7z-recover.service

[Service]
Environment=ALSA_CARD=whisplaysound
Environment=AUDIODEV=whisplaysound
Environment=SDL_AUDIODRIVER=alsa
Environment=WHISPLAY_ALSA_CARD=whisplaysound
Environment=WHISPLAY_ALSA_PLAYBACK_DEVICE=whisplaysound
Environment=WHISPLAY_ALSA_CAPTURE_DEVICE=whisplaysound
Environment=WHISPLAY_AUDIO_RATE=48000
EOF
  systemctl daemon-reload
  ok "Installed A7Z-only daemon audio environment: $dropin_path"
}

need_root
is_radxa_cubie_a7z || \
  die "This installer only supports Radxa Cubie A7Z."

[[ -f "$SOUNDCARD_DIR/scripts/install.sh" ]] || \
  die "Missing unified sound card installer: $SOUNDCARD_DIR/scripts/install.sh"

echo "=============================================="
echo " Whisplay HAT Driver Install - Radxa Cubie A7Z"
echo "=============================================="
echo
echo "Detected platform: $(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
echo "Sound driver: $SOUNDCARD_DIR"
echo
warn "Do not press the Whisplay HAT button on A7Z; it can cut board power."
echo

install_platform_deps

echo
log "Enabling SPI1 overlay for the LCD..."
enable_overlay_copy "sun60iw2p1-spi1-spidev.dtbo" "SPI1"

echo
log "Enabling TWI7 overlay for the 40-pin I2C pins..."
enable_overlay_copy "sun60iw2p1-twi7.dtbo" "TWI7"

echo
log "Installing the unified Whisplay sound card driver..."
WHISPLAY_PLATFORM=radxa_cubie_a7z bash "$SOUNDCARD_DIR/scripts/install.sh"

echo
log "Configuring daemon apps to use the A7Z Whisplay sound card..."
install_daemon_audio_dropin

detect_interfaces

echo
echo "=============================================="
echo " Installation complete."
echo "=============================================="
echo
echo "Reboot to activate the unified overlay:"
echo "  sudo reboot"
echo
echo "After reboot, verify:"
echo "  aplay -l | grep -i whisplay"
echo "  amixer -c whisplaysound cget name='speaker'"
echo "  amixer -c whisplaysound cget name='mic'"
echo
echo "A7Z supports the 48 kHz sample-rate family on this I2S path."
echo "Run the hardware test with 48 kHz audio:"
echo "  cd $PROJECT_ROOT/example"
echo "  sudo bash run_test.sh"
