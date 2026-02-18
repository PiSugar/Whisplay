#!/usr/bin/env bash
#
# Safer WM8960 installer for PiSugar Whisplay
# - Preflight checks BEFORE any changes
# - Explicit user confirmation
# - Timestamped backups of touched files
# - Every operation fails fast with a clear summary
# - Patches upstream wm8960-soundcard script so alsactl restore can’t fail the service
# - Optional: post-run power/brownout warning (non-fatal)
#
# Drop this next to WM8960-Audio-HAT.zip and run:
#   sudo bash install_wm8960_drive.sh
#

set -Eeuo pipefail

# ----------------------------
# Config
# ----------------------------
ZIP_NAME="WM8960-Audio-HAT.zip"
WORKDIR_NAME="WM8960-Audio-HAT"

BOOT_CONFIG="/boot/firmware/config.txt"
MODULES_FILE="/etc/modules"

TARGET_ETC_DIR="/etc/wm8960-soundcard"
TARGET_BIN="/usr/bin/wm8960-soundcard"
TARGET_SERVICE="/lib/systemd/system/wm8960-soundcard.service"
ALT_SERVICE="/usr/lib/systemd/system/wm8960-soundcard.service"

REQUIRED_PKGS=(
  alsa-utils
  i2c-tools
  dkms
  libasound2-plugins
  unzip
  raspi-config
)

# ----------------------------
# State tracking for summary
# ----------------------------
declare -A STEP
STEP[preflight]="not started"
STEP[apt_update]="not started"
STEP[pkgs_install]="not started"
STEP[enable_spi]="not started"
STEP[unzip]="not started"
STEP[modules]="not started"
STEP[boot_config]="not started"
STEP[install_files]="not started"
STEP[patch_upstream]="not started"
STEP[service]="not started"
STEP[alsactl_restore]="not started"
STEP[done]="not started"

BACKUP_DIR=""

# ----------------------------
# Helpers
# ----------------------------
log()  { echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }
die()  { echo "[X] $*" >&2; exit 1; }

is_pi() {
  [[ -r /proc/device-tree/model ]] || return 1
  grep -q "Raspberry Pi" /proc/device-tree/model
}

need_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "This script must be run as root (use sudo)."
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }
pkg_missing() { ! dpkg -s "$1" >/dev/null 2>&1; }

backup_file() {
  local f="$1"
  [[ -e "$f" ]] || return 0
  cp -a "$f" "$BACKUP_DIR/" || die "Failed to backup: $f"
}

ensure_line_in_file() {
  local file="$1"
  local line="$2"
  grep -qxF "$line" "$file" || echo "$line" >> "$file"
}

safe_uncomment_or_append() {
  local file="$1"
  local line="$2"
  local commented="#$line"

  if grep -qxF "$commented" "$file"; then
    # replace exact commented line with the line
    sed -i "s|^$(printf '%s' "$commented" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$|$line|" "$file"
  else
    ensure_line_in_file "$file" "$line"
  fi
}

enable_spi() {
  log "Attempting to enable SPI via raspi-config..."
  have_cmd raspi-config || die "raspi-config not found. Install it or enable SPI manually."
  raspi-config nonint do_spi 0
}

patch_upstream_wm8960_script() {
  # Goal: ensure the upstream /usr/bin/wm8960-soundcard can never fail the service
  # due to alsactl restore returning non-zero (common on Lite/minimal images).
  STEP[patch_upstream]="running"

  [[ -x "$TARGET_BIN" ]] || die "Expected executable not found: $TARGET_BIN"

  # If already patched, do nothing
  if grep -qE 'alsactl restore .*continuing anyway' "$TARGET_BIN"; then
    ok "Upstream wm8960-soundcard already patched."
    STEP[patch_upstream]="ok"
    return 0
  fi

  # Replace a line that is exactly: alsactl restore
  # with a safe block that logs but exits 0.
  if grep -qxF "alsactl restore" "$TARGET_BIN"; then
    # Make a safety backup in our backup dir
    cp -a "$TARGET_BIN" "$BACKUP_DIR/" || die "Failed to backup $TARGET_BIN before patching"

    # Use awk to do an exact-line replacement (portable, avoids sed multiline pain)
    awk '
      $0 == "alsactl restore" {
        print "if ! alsactl restore; then"
        print "    echo \"[WARN] alsactl restore failed — continuing anyway\""
        print "fi"
        next
      }
      { print }
    ' "$TARGET_BIN" > "${TARGET_BIN}.tmp" || die "Failed to patch $TARGET_BIN"
    mv "${TARGET_BIN}.tmp" "$TARGET_BIN" || die "Failed to install patched $TARGET_BIN"
    chmod 0755 "$TARGET_BIN" || die "Failed to chmod $TARGET_BIN"
    ok "Patched $TARGET_BIN so alsactl restore can’t fail the service."
  else
    warn "Did not find an exact 'alsactl restore' line in $TARGET_BIN. Not patching."
    warn "If the service fails, inspect /var/log/wm8960-soundcard.log."
  fi

  STEP[patch_upstream]="ok"
}

power_warning() {
  local warned=0

  if have_cmd vcgencmd; then
    local t
    t="$(vcgencmd get_throttled 2>/dev/null || true)"
    if [[ "$t" =~ throttled=0x([0-9a-fA-F]+) ]]; then
      if [[ "${BASH_REMATCH[1]}" != "0" ]]; then
        warn "Power/thermal flags since boot: $t"
        warn "This can indicate undervoltage/brownouts. Consider a stronger PSU/cable."
        warned=1
      fi
    fi
  fi

  if dmesg 2>/dev/null | grep -qiE "under-voltage|undervoltage|brownout|throttl"; then
    warn "Kernel log contains power-related warnings since boot (undervoltage/throttling)."
    warned=1
  fi

  [[ "$warned" -eq 0 ]] && ok "No obvious power warnings detected since boot."
}

print_summary() {
  echo
  echo "================ Summary ================"
  for k in preflight apt_update pkgs_install enable_spi unzip modules boot_config install_files patch_upstream service alsactl_restore done; do
    printf "%-16s : %s\n" "$k" "${STEP[$k]}"
  done
  [[ -n "$BACKUP_DIR" ]] && echo "Backups saved in  : $BACKUP_DIR"
  echo "========================================="
}

on_error() {
  STEP[done]="failed"
  warn "Installer failed (line $1)."
  print_summary
  echo
  warn "Nothing was rolled back automatically. Use backups if you need to revert."
  exit 1
}
trap 'on_error $LINENO' ERR

# ----------------------------
# Preflight (NO changes)
# ----------------------------
need_root
is_pi || die "This installer only supports Raspberry Pi."

STEP[preflight]="running"

have_cmd apt-get || die "apt-get not found."
have_cmd dpkg    || die "dpkg not found."

[[ -e "$BOOT_CONFIG" ]] || die "Expected boot config not found at: $BOOT_CONFIG"
[[ -w "$BOOT_CONFIG" ]] || die "Boot config is not writable: $BOOT_CONFIG"

[[ -f "$ZIP_NAME" ]] || die "Missing $ZIP_NAME in current directory: $(pwd)"

missing=()
for p in "${REQUIRED_PKGS[@]}"; do
  if pkg_missing "$p"; then
    missing+=("$p")
  fi
done

echo
echo "This installer will:"
echo "  1) Run: apt-get update"
echo "  2) Ensure SPI is enabled (via raspi-config)"
echo "  3) Install missing packages (if any): ${missing[*]:-(none)}"
echo "  4) Unzip $ZIP_NAME and install WM8960 config/service"
echo "  5) Edit: $BOOT_CONFIG  (with a timestamped backup)"
echo "  6) Edit: $MODULES_FILE (with a timestamped backup)"
echo "  7) Patch $TARGET_BIN so alsactl restore can’t fail the service"
echo
read -r -p "Proceed? [y/N] " ans
ans="${ans:-N}"
[[ "$ans" =~ ^[Yy]$ ]] || die "Cancelled by user."

STEP[preflight]="ok"

# ----------------------------
# Backups (before any edits)
# ----------------------------
ts="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/backups/whisplay-wm8960-$ts"
mkdir -p "$BACKUP_DIR"
ok "Backups will be written to: $BACKUP_DIR"

backup_file "$BOOT_CONFIG"
backup_file "$MODULES_FILE"
backup_file /etc/asound.conf
backup_file /var/lib/alsa/asound.state
backup_file "$TARGET_BIN"
backup_file "$TARGET_SERVICE"
backup_file "$ALT_SERVICE"

# ----------------------------
# Execute steps (fail fast)
# ----------------------------
STEP[apt_update]="running"
apt-get update
STEP[apt_update]="ok"

STEP[pkgs_install]="running"
if [[ "${#missing[@]}" -gt 0 ]]; then
  apt-get install -y "${missing[@]}"
fi
STEP[pkgs_install]="ok"

STEP[enable_spi]="running"
enable_spi
STEP[enable_spi]="ok"

STEP[unzip]="running"
rm -rf "$WORKDIR_NAME"
unzip -o "$ZIP_NAME"
cd "$WORKDIR_NAME"
STEP[unzip]="ok"

STEP[modules]="running"
[[ -e "$MODULES_FILE" ]] || touch "$MODULES_FILE"
ensure_line_in_file "$MODULES_FILE" "i2c-dev"
ensure_line_in_file "$MODULES_FILE" "snd-soc-wm8960"
ensure_line_in_file "$MODULES_FILE" "snd-soc-wm8960-soundcard"
STEP[modules]="ok"

STEP[boot_config]="running"
safe_uncomment_or_append "$BOOT_CONFIG" "dtparam=i2c_arm=on"
safe_uncomment_or_append "$BOOT_CONFIG" "dtparam=i2s=on"
ensure_line_in_file "$BOOT_CONFIG" "dtoverlay=i2s-mmap"
ensure_line_in_file "$BOOT_CONFIG" "dtoverlay=wm8960-soundcard"
STEP[boot_config]="ok"

STEP[install_files]="running"
mkdir -p "$TARGET_ETC_DIR"
cp -f ./*.conf "$TARGET_ETC_DIR/"
cp -f ./*.state "$TARGET_ETC_DIR/"
cp -f ./wm8960-soundcard /usr/bin/
# Service path varies a bit between distros; keep PiSugar’s original location
cp -f ./wm8960-soundcard.service /lib/systemd/system/ || cp -f ./wm8960-soundcard.service /usr/lib/systemd/system/
chmod 0755 /usr/bin/wm8960-soundcard
STEP[install_files]="ok"

# IMPORTANT: patch BEFORE starting service
patch_upstream_wm8960_script

STEP[service]="running"
systemctl daemon-reload
systemctl enable wm8960-soundcard.service
systemctl restart wm8960-soundcard.service

# Verify: treat "inactive/failed" as a real install failure
if ! systemctl is-active --quiet wm8960-soundcard.service; then
  warn "wm8960-soundcard.service is not active after restart."
  systemctl status wm8960-soundcard.service --no-pager -l || true
  if [[ -f /var/log/wm8960-soundcard.log ]]; then
    warn "Last 200 lines of /var/log/wm8960-soundcard.log:"
    tail -n 200 /var/log/wm8960-soundcard.log || true
  fi
  die "Service failed to start."
fi
STEP[service]="ok"

STEP[alsactl_restore]="running"
# Optional: restore state now; do not fail installer on non-zero exit
if ! alsactl restore -c wm8960soundcard; then
  warn "alsactl restore -c wm8960soundcard returned non-zero (continuing)."
fi
STEP[alsactl_restore]="ok"

STEP[done]="ok"

# ----------------------------
# Wrap-up
# ----------------------------
print_summary
echo
echo "--------------------------------------------------------------"
echo "Reboot recommended to apply all settings cleanly."
echo "  sudo reboot"
echo "--------------------------------------------------------------"
echo
echo "In order to run the python demos, you may need"
echo "    sudo apt install python3-pil python3-numpy python3-pygame" 
echo "--------------------------------------------------------------"

power_warning
