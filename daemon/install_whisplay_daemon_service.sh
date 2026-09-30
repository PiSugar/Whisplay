#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-$(whoami)}"
USER_HOME="$(eval echo "~${TARGET_USER}")"
TARGET_UID="$(id -u "$TARGET_USER")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXAMPLE_DIR="$PROJECT_ROOT/example"
DEFAULT_APPS_SRC_DIR="$PROJECT_ROOT/daemon/default_apps"
DAEMON_HOME="$USER_HOME/.whisplay-daemon"
APPS_DIR="$DAEMON_HOME/app"
SETTINGS_PATH="$DAEMON_HOME/settings.json"
PYTHON_BIN="$(command -v python3)"
SYSTEMCTL_BIN="$(command -v systemctl || true)"

configure_orangepi_device_access() {
  local compat=""
  local release=""
  local boot_env=""

  if [ -r /proc/device-tree/compatible ]; then
    compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"
  fi
  [ -r /etc/orangepi-release ] && release="$(cat /etc/orangepi-release 2>/dev/null || true)"
  [ -r /boot/orangepiEnv.txt ] && boot_env="$(cat /boot/orangepiEnv.txt 2>/dev/null || true)"
  if ! echo "$compat" | grep -qi 'xunlong,orangepi-zero2w' && \
     ! echo "$release" | grep -qi '^BOARD=orangepizero3w$' && \
     ! echo "$boot_env" | grep -qi 'orangepi-zero3w\.dtb'; then
    return 0
  fi

  echo "Configuring Orange Pi GPIO/SPI access for $TARGET_USER..."
  getent group gpio >/dev/null 2>&1 || sudo groupadd --system gpio
  sudo usermod -aG gpio "$TARGET_USER"
  sudo tee /etc/udev/rules.d/60-whisplay-orangepi.rules >/dev/null <<'EOF'
SUBSYSTEM=="gpio", KERNEL=="gpiochip[0-9]*", GROUP="gpio", MODE="0660"
SUBSYSTEM=="spidev", KERNEL=="spidev[0-9]*.[0-9]*", GROUP="gpio", MODE="0660"
EOF
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=gpio || true
  sudo udevadm trigger --subsystem-match=spidev || true
  sudo chgrp gpio /dev/gpiochip* /dev/spidev* 2>/dev/null || true
  sudo chmod g+rw /dev/gpiochip* /dev/spidev* 2>/dev/null || true
}

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: python3 not found."
  exit 1
fi

if [ -z "$SYSTEMCTL_BIN" ]; then
  echo "Error: systemctl not found."
  exit 1
fi

echo "Ensuring python3-numpy is installed (for fast RGB565 conversion)..."
if ! "$PYTHON_BIN" -c "import numpy" 2>/dev/null; then
  sudo apt-get install -y python3-numpy || echo "Warning: failed to install python3-numpy, falling back to pure-Python RGB565"
fi

echo "Ensuring python3-smbus is installed (for PiSugar 3 power-button detection)..."
if ! "$PYTHON_BIN" -c "import smbus" 2>/dev/null; then
  sudo apt-get install -y python3-smbus || echo "Warning: failed to install python3-smbus; PiSugar 3 power-button detection will be unavailable"
fi

echo "Ensuring ffmpeg is installed (required by play_mp4 app)..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  sudo apt-get install -y ffmpeg || echo "Warning: failed to install ffmpeg; play_mp4 will not work until ffmpeg is available"
fi

if [ "$TARGET_USER" = "root" ] && [ -z "${SUDO_USER:-}" ]; then
  echo "Error: run this script as your normal user or via sudo preserving SUDO_USER."
  exit 1
fi

echo "Installing whisplay-daemon.service for user: $TARGET_USER"

configure_orangepi_device_access

install -d -m 0755 "$APPS_DIR"

cat > "$SETTINGS_PATH" <<EOF
{
  "apps_dir": "$APPS_DIR"
}
EOF

if [ -d "$DEFAULT_APPS_SRC_DIR" ]; then
  for template_path in "$DEFAULT_APPS_SRC_DIR"/*.json; do
    [ -f "$template_path" ] || continue
    target_path="$APPS_DIR/$(basename "$template_path")"
    sed "s|__EXAMPLE_DIR__|$EXAMPLE_DIR|g" "$template_path" > "$target_path"
  done
fi

chown -R "$TARGET_USER":"$TARGET_USER" "$DAEMON_HOME"

# The daemon runs unprivileged. Grant only the two fixed power operations used
# by its built-in System app; no shell or arbitrary systemctl command is allowed.
POWER_SUDOERS_TMP="$(mktemp)"
trap 'rm -f "$POWER_SUDOERS_TMP"' EXIT
printf '%s ALL=(root) NOPASSWD: %s poweroff, %s reboot\n' \
  "$TARGET_USER" "$SYSTEMCTL_BIN" "$SYSTEMCTL_BIN" > "$POWER_SUDOERS_TMP"
sudo visudo -cf "$POWER_SUDOERS_TMP"
sudo install -o root -g root -m 0440 "$POWER_SUDOERS_TMP" /etc/sudoers.d/whisplay-daemon-power

sudo tee /etc/systemd/system/whisplay-daemon.service > /dev/null <<EOF
[Unit]
Description=Whisplay Hardware Daemon
After=network.target

[Service]
Type=simple
User=$TARGET_USER
Group=audio
SupplementaryGroups=audio video gpio input
WorkingDirectory=$PROJECT_ROOT
ExecStart=$PYTHON_BIN $PROJECT_ROOT/daemon/whisplay_daemon.py
Environment=HOME=$USER_HOME
Environment=XDG_RUNTIME_DIR=/run/user/$TARGET_UID
Environment=PYTHONUNBUFFERED=1
PrivateDevices=no
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable whisplay-daemon.service
sudo systemctl restart whisplay-daemon.service
sudo systemctl status whisplay-daemon.service --no-pager
