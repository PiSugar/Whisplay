#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-$(whoami)}"
USER_HOME="$(eval echo "~${TARGET_USER}")"
TARGET_UID="$(id -u "$TARGET_USER")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3)"

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: python3 not found."
  exit 1
fi

if [ "$TARGET_USER" = "root" ] && [ -z "${SUDO_USER:-}" ]; then
  echo "Error: run this script as your normal user or via sudo preserving SUDO_USER."
  exit 1
fi

echo "Installing whisplay-daemon.service for user: $TARGET_USER"

sudo tee /etc/systemd/system/whisplay-daemon.service > /dev/null <<EOF
[Unit]
Description=Whisplay Hardware Daemon
After=network.target

[Service]
Type=simple
User=$TARGET_USER
Group=audio
SupplementaryGroups=audio video gpio
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN $SCRIPT_DIR/whisplay_daemon.py
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
