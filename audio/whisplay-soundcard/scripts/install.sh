#!/usr/bin/env bash
# Build, install and activate Whisplay unified sound card driver.
# Supports ES8389 (0x10) and WM8960 (0x1a) auto-detection.
#
# Usage (on the target board, from a clone of this repo):
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

detect_platform() {
    local model=""
    local compat=""

    if [[ -r /proc/device-tree/model ]]; then
        model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
    fi
    if [[ -r /proc/device-tree/compatible ]]; then
        compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"
    fi

    if [[ "$model" == *"Raspberry Pi"* ]]; then
        echo "raspberry_pi"
        return 0
    fi
    if [[ "$model" == *"Cubie"* ]] || echo "$compat" | grep -qi "cubie-a7z"; then
        echo "unknown"
        return 0
    fi
    if [[ "$model" == *"Radxa"* ]] || echo "$compat" | grep -qi "radxa"; then
        echo "radxa_zero3w"
        return 0
    fi

    echo "unknown"
}

install_build_deps() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    if [[ "$PLATFORM" == "raspberry_pi" ]]; then
        if apt-get install -y -qq raspberrypi-kernel-headers device-tree-compiler \
                alsa-utils libasound2-plugins sox 2>/dev/null; then
            return
        fi
    fi

    if apt-get install -y -qq "linux-headers-$(uname -r)" device-tree-compiler \
            alsa-utils libasound2-plugins sox; then
        return
    fi

    echo "  WARN: could not install kernel headers automatically." >&2
    echo "  Install headers manually, then re-run this script." >&2
}

install_overlay() {
    local dts
    local dtbo
    local boot_cfg

    case "$PLATFORM" in
        raspberry_pi)
            dts="$SRC/dts/whisplay-soundcard.dts"
            dtbo="$SRC/dts/whisplay-soundcard.dtbo"
            dtc -I dts -O dtb -@ -o "$dtbo" "$dts"
            install -m 644 "$dtbo" /boot/firmware/overlays/
            install -m 644 "$dtbo" /boot/overlays/ 2>/dev/null || true

            boot_cfg="/boot/firmware/config.txt"
            test -f "$boot_cfg" || boot_cfg="/boot/config.txt"
            for param in i2c_arm=on i2s=on; do
                if ! grep -q "^dtparam=${param}" "$boot_cfg" 2>/dev/null; then
                    echo "dtparam=${param}" >>"$boot_cfg"
                fi
            done
            if ! grep -q "^dtoverlay=whisplay-soundcard" "$boot_cfg" 2>/dev/null; then
                echo "dtoverlay=whisplay-soundcard" >>"$boot_cfg"
            fi
            sed -i '/^dtoverlay=wm8960-soundcard/d' "$boot_cfg" 2>/dev/null || true
            sed -i '/^dtoverlay=es8389-soundcard/d' "$boot_cfg" 2>/dev/null || true
            ;;
        radxa_zero3w)
            dts="$SRC/dts/whisplay-soundcard-radxa-zero3w.dts"
            dtbo="/boot/dtbo/whisplay-soundcard-radxa-zero3w.dtbo"
            mkdir -p /boot/dtbo
            dtc -I dts -O dtb -@ -o "$dtbo" "$dts"

            if [[ -f /boot/dtbo/rk3568-i2s3-m0.dtbo ]]; then
                mv /boot/dtbo/rk3568-i2s3-m0.dtbo /boot/dtbo/rk3568-i2s3-m0.dtbo.disabled
                echo "  Disabled conflicting I2S3 dummy-sound overlay"
            fi
            if [[ -f /boot/dtbo/wm8960-radxa-zero3.dtbo ]]; then
                mv /boot/dtbo/wm8960-radxa-zero3.dtbo /boot/dtbo/wm8960-radxa-zero3.dtbo.disabled
                echo "  Disabled legacy Radxa ZERO 3W WM8960 simple-card overlay"
            fi

            grep -q "i2c-dev" /etc/modules 2>/dev/null || echo "i2c-dev" >>/etc/modules
            grep -q "snd-soc-wm8960" /etc/modules 2>/dev/null || echo "snd-soc-wm8960" >>/etc/modules
            grep -q "snd-soc-whisplay-soundcard" /etc/modules 2>/dev/null || \
                echo "snd-soc-whisplay-soundcard" >>/etc/modules

            sed -i '/wm8960-radxa-zero3/d' /boot/extlinux/extlinux.conf 2>/dev/null || true
            if command -v u-boot-update >/dev/null 2>&1; then
                u-boot-update
            else
                echo "  WARN: u-boot-update not found; verify /boot/extlinux/extlinux.conf manually." >&2
            fi
            ;;
        *)
            echo "Unsupported platform for overlay install: $PLATFORM" >&2
            exit 1
            ;;
    esac
}

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
PLATFORM="${WHISPLAY_PLATFORM:-$(detect_platform)}"
echo "Platform: $PLATFORM"
echo

echo "[1/6] Installing build dependencies ..."
install_build_deps

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
install_overlay

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
