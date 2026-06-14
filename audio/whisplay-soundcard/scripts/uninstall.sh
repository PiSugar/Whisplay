#!/usr/bin/env bash
# Remove Whisplay driver, overlay, and ALSA config.

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

echo "===================================="
echo " Whisplay Sound Card Uninstaller"
echo "===================================="

rm -f /etc/modprobe.d/whisplay-calib.conf

systemctl disable --now whisplay-soundcard-warmup.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/whisplay-soundcard-warmup.service
systemctl daemon-reload >/dev/null 2>&1 || true

BOOT_CFG="/boot/firmware/config.txt"
test -f "$BOOT_CFG" || BOOT_CFG="/boot/config.txt"
sed -i '/^dtoverlay=whisplay-soundcard/d' "$BOOT_CFG" 2>/dev/null || true

rm -f /boot/firmware/overlays/whisplay-soundcard.dtbo
rm -f /boot/overlays/whisplay-soundcard.dtbo 2>/dev/null || true

KVER="$(uname -r)"
rm -f "/lib/modules/${KVER}/kernel/sound/soc/codecs/snd-soc-whisplay-soundcard.ko"
depmod -a

rm -f /etc/asound.conf

echo
echo "Uninstall complete. Reboot to unload the overlay:"
echo "  sudo reboot"
echo "===================================="
