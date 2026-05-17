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
