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

configure_orangepi_device_access() {
  local compat=""

  if [ -r /proc/device-tree/compatible ]; then
    compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"
  fi
  echo "$compat" | grep -qi 'xunlong,orangepi-zero2w' || return 0

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

echo "Ensuring python3-numpy is installed (for fast RGB565 conversion)..."
if ! "$PYTHON_BIN" -c "import numpy" 2>/dev/null; then
  sudo apt-get install -y python3-numpy || echo "Warning: failed to install python3-numpy, falling back to pure-Python RGB565"
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
