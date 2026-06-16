#!/usr/bin/env bash
# Build, install and activate Whisplay unified sound card driver.
# Supports ES8389 (0x10) and WM8960 (0x1a) auto-detection.
#
# Usage (on the Raspberry Pi, from a clone of this repo):
#   sudo bash scripts/install.sh
#
# Optional: keep legacy mixer controls visible for LUT lab work:
#   sudo WHISPLAY_CALIB_MODE=1 bash scripts/install.sh

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SRC="$ROOT/src"
CFG="$ROOT/configs"

migrate_legacy_alsa_refs() {
    local file

    for file in /etc/asound.conf /root/.asoundrc /home/*/.asoundrc; do
        [[ -f "$file" ]] || continue

        if grep -Eq 'wm8960soundcard|es8389soundcard' "$file"; then
            sed -i \
                -e 's/wm8960soundcard/whisplaysound/g' \
                -e 's/es8389soundcard/whisplaysound/g' \
                "$file"
            echo "  Migrated legacy ALSA card references in $file"
        fi
    done
}

echo "===================================="
echo " Whisplay Sound Card Installer"
echo "===================================="
echo "Source: $ROOT"
echo

echo "[1/6] Installing build dependencies ..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
if apt-get install -y -qq raspberrypi-kernel-headers device-tree-compiler \
        alsa-utils libasound2-plugins sox 2>/dev/null; then
    :
elif apt-get install -y -qq "linux-headers-$(uname -r)" device-tree-compiler \
        alsa-utils libasound2-plugins sox; then
    :
else
    echo "  WARN: could not install kernel headers automatically." >&2
    echo "  Install headers manually, then re-run this script." >&2
fi

echo
echo "[2/6] Building snd-soc-whisplay-soundcard.ko ..."
make -C "$SRC"

echo
echo "[3/6] Installing kernel module ..."
KVER="$(uname -r)"
install -m 644 "$SRC/snd-soc-whisplay-soundcard.ko" \
    "/lib/modules/${KVER}/kernel/sound/soc/codecs/"
depmod -a

echo
echo "[4/6] Compiling and installing device-tree overlay ..."
dtc -I dts -O dtb -@ -o "$SRC/dts/whisplay-soundcard.dtbo" "$SRC/dts/whisplay-soundcard.dts"
install -m 644 "$SRC/dts/whisplay-soundcard.dtbo" /boot/firmware/overlays/
install -m 644 "$SRC/dts/whisplay-soundcard.dtbo" /boot/overlays/ 2>/dev/null || true

BOOT_CFG="/boot/firmware/config.txt"
test -f "$BOOT_CFG" || BOOT_CFG="/boot/config.txt"

for param in i2c_arm=on i2s=on; do
    if ! grep -q "^dtparam=${param}" "$BOOT_CFG" 2>/dev/null; then
        echo "dtparam=${param}" >>"$BOOT_CFG"
    fi
done

if ! grep -q "^dtoverlay=whisplay-soundcard" "$BOOT_CFG" 2>/dev/null; then
    echo "dtoverlay=whisplay-soundcard" >>"$BOOT_CFG"
fi

# Remove legacy Waveshare overlays that conflict with Whisplay
sed -i '/^dtoverlay=wm8960-soundcard/d' "$BOOT_CFG" 2>/dev/null || true
sed -i '/^dtoverlay=es8389-soundcard/d' "$BOOT_CFG" 2>/dev/null || true

sed -i '/snd-soc-wm8960-soundcard/d' /etc/modules 2>/dev/null || true
systemctl disable --now wm8960-soundcard.service >/dev/null 2>&1 || true
systemctl disable --now es8389-soundcard.service >/dev/null 2>&1 || true
systemctl disable --now es8389-defaults.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/sysinit.target.wants/wm8960-soundcard.service
rm -f /etc/systemd/system/sysinit.target.wants/es8389-soundcard.service
rm -f /etc/systemd/system/multi-user.target.wants/es8389-defaults.service
rm -f /etc/systemd/system/es8389-defaults.service
rm -f /etc/wireplumber/main.lua.d/51-es8389.lua
rm -rf /etc/wm8960-soundcard /etc/es8389-soundcard
if [ -L /var/lib/alsa/asound.state ]; then
    case "$(readlink /var/lib/alsa/asound.state)" in
        *wm8960-soundcard*|*es8389-soundcard*) rm -f /var/lib/alsa/asound.state ;;
    esac
fi

echo
echo "[5/6] Installing ALSA configuration ..."
rm -f /etc/asound.conf
install -m 644 "$CFG/asound.conf" /etc/asound.conf
migrate_legacy_alsa_refs

echo
echo "[6/7] Module options ..."
if [[ "${WHISPLAY_CALIB_MODE:-0}" == "1" ]]; then
    echo 'options snd-soc-whisplay-soundcard skip_legacy_hide=1' \
        >/etc/modprobe.d/whisplay-calib.conf
    echo "  Calibration mode: legacy ALSA controls stay visible (skip_legacy_hide=1)"
else
    rm -f /etc/modprobe.d/whisplay-calib.conf
    rm -f /etc/modprobe.d/whisplay-soundcard.conf
    rm -f /etc/modprobe.d/blacklist-whisplay.conf
    echo "  Production mode: legacy controls hidden after boot (~3 s)"
fi

echo
echo "[7/7] Installing boot defaults ..."
cat >/etc/systemd/system/whisplay-soundcard-warmup.service <<'EOF'
[Unit]
Description=Whisplay Sound Card boot setup
After=sound.target alsa-restore.service multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'for i in $(seq 1 30); do aplay -l 2>/dev/null | grep -qi "whisplaysound" && break; sleep 1; done; aplay -l 2>/dev/null | grep -qi "whisplaysound" || exit 0; amixer -c whisplaysound cset name="speaker" 80 >/dev/null 2>&1 || true; amixer -c whisplaysound cset name="mic" 80 >/dev/null 2>&1 || true; aplay -l 2>/dev/null | grep -qi "whisplaysound.*wm8960" || exit 0; sleep 8; timeout 3 arecord -q -D hw:whisplaysound -f S16_LE -r 48000 -c 2 -d 1 /dev/null >/dev/null 2>&1 || true'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable whisplay-soundcard-warmup.service >/dev/null
echo "  Boot defaults enabled (speaker=80, mic=80)"

echo
echo "===================================="
echo " Installation complete."
echo
echo " Reboot to load the driver and overlay:"
echo "   sudo reboot"
echo
echo " After reboot:"
echo "   aplay -l | grep -i whisplay"
echo "   amixer -c whisplaysound controls"
echo "   amixer -c whisplaysound cget name='speaker'"
echo
echo " Quick loopback test:"
echo "   sox -n -r 48000 -c 2 -b 16 /tmp/t.wav synth 2 sine 440"
echo "   amixer -c whisplaysound cset name='speaker' 80"
echo "   aplay -D whisplaysound /tmp/t.wav"
echo "===================================="
