#!/bin/bash
#
# Whisplay HAT Driver Install Script - Radxa ZERO 3W
# Features: Enable SPI/I2C/I2S overlays, install Python dependencies,
#           compile and install WM8960 audio driver kernel module, configure sound card
#

set -e

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 1>&2
   exit 1
fi

# Detect if running on Radxa platform
is_Radxa=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' | grep -i "Radxa" || true)
if [ -z "${is_Radxa}" ]; then
  echo "Error: This script is only for Radxa platform"
  echo "For Raspberry Pi, use install_wm8960_drive.sh"
  exit 1
fi

echo "=============================================="
echo " Whisplay HAT Driver Install - Radxa ZERO 3W"
echo "=============================================="
echo ""
echo "Detected platform: $(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DTBO_DIR="/boot/dtbo"

# ==================== 1. Install System Dependencies ====================
echo "[1/8] Installing system dependencies..."
apt-get update -y
apt-get install -y \
    python3-libgpiod \
    python3-spidev \
    python3-pil \
    python3-pygame \
    i2c-tools \
    device-tree-compiler \
    alsa-utils \
    unzip \
    make \
    gcc \
    wget

echo "  System dependencies installed"

# ==================== 2. Enable SPI3_M1 Overlay ====================
echo ""
echo "[2/8] Enabling SPI3 Overlay (for LCD display)..."

SPI_OVERLAY="rk3568-spi3-m1-cs0-spidev.dtbo"
SPI_OVERLAY_DISABLED="${SPI_OVERLAY}.disabled"

if [ -f "${DTBO_DIR}/${SPI_OVERLAY}" ]; then
    echo "  SPI3 Overlay already enabled"
elif [ -f "${DTBO_DIR}/${SPI_OVERLAY_DISABLED}" ]; then
    cp "${DTBO_DIR}/${SPI_OVERLAY_DISABLED}" "${DTBO_DIR}/${SPI_OVERLAY}"
    echo "  SPI3 Overlay enabled: ${SPI_OVERLAY}"
else
    echo "  Warning: SPI3 Overlay file not found"
    echo "  Please enable manually: rsetup -> Overlay -> Enable ${SPI_OVERLAY}"
fi

# ==================== 3. Enable I2C3_M0 Overlay ====================
echo ""
echo "[3/8] Enabling I2C3-M0 Overlay (for 40-pin header Pin3/5 I2C)..."

# Radxa ZERO 3W 40-pin header Pin3(SDA)/Pin5(SCL) maps to I2C3 M0 group
# Note: I2C3 M1 group (GPIO3_B5/B6) is used by onboard fusb302 (USB-C)
# I2C3 M0 group (GPIO1_A0/A1) is the 40-pin header I2C
I2C_OVERLAY="rk3568-i2c3-m0.dtbo"
I2C_OVERLAY_DISABLED="${I2C_OVERLAY}.disabled"

if [ -f "${DTBO_DIR}/${I2C_OVERLAY}" ]; then
    echo "  I2C3-M0 Overlay already enabled"
elif [ -f "${DTBO_DIR}/${I2C_OVERLAY_DISABLED}" ]; then
    cp "${DTBO_DIR}/${I2C_OVERLAY_DISABLED}" "${DTBO_DIR}/${I2C_OVERLAY}"
    echo "  I2C3-M0 Overlay enabled: ${I2C_OVERLAY}"
else
    echo "  Warning: I2C3-M0 Overlay file not found"
    echo "  Please enable manually: rsetup -> Overlay -> Enable ${I2C_OVERLAY}"
fi

# ==================== 4. Configure WM8960 Audio Overlay ====================
echo ""
echo "[4/8] Configuring WM8960 audio overlay..."

# Disable I2S3 dummy-sound overlay (conflicts with WM8960 overlay)
# WM8960 overlay enables I2S3 and connects to WM8960 codec on its own
I2S_DUMMY_OVERLAY="rk3568-i2s3-m0.dtbo"
if [ -f "${DTBO_DIR}/${I2S_DUMMY_OVERLAY}" ]; then
    mv "${DTBO_DIR}/${I2S_DUMMY_OVERLAY}" "${DTBO_DIR}/${I2S_DUMMY_OVERLAY}.disabled"
    echo "  Disabled I2S3 dummy-sound overlay (WM8960 overlay configures I2S3 itself)"
else
    echo "  I2S3 dummy-sound overlay already disabled"
fi

# Compile WM8960 device tree overlay
echo "  Compiling WM8960 device tree overlay..."
WM8960_DTS="${SCRIPT_DIR}/wm8960-radxa-zero3.dts"

if [ -f "${WM8960_DTS}" ]; then
    WM8960_DTBO="${DTBO_DIR}/wm8960-radxa-zero3.dtbo"
    if dtc -@ -I dts -O dtb -o "${WM8960_DTBO}" "${WM8960_DTS}" 2>/dev/null; then
        echo "  WM8960 overlay compiled and installed to ${WM8960_DTBO}"
    else
        echo "  Warning: WM8960 overlay compilation failed"
        echo "  Please check ${WM8960_DTS} and compile manually"
    fi
else
    echo "  WM8960 DTS file not found, skipping overlay compilation"
fi

# Configure auto-load modules
grep -q "i2c-dev" /etc/modules || echo "i2c-dev" >> /etc/modules
grep -q "snd-soc-wm8960" /etc/modules || echo "snd-soc-wm8960" >> /etc/modules

# ==================== 5. Compile WM8960 Kernel Module ====================
echo ""
echo "[5/8] Compiling WM8960 kernel module..."

WM8960_MODULE_AVAILABLE=false
if modinfo snd-soc-wm8960 &>/dev/null; then
    WM8960_MODULE_AVAILABLE=true
    echo "  snd-soc-wm8960 kernel module already available, skipping compilation"
fi

if [ "$WM8960_MODULE_AVAILABLE" = false ]; then
    KVER=$(uname -r)
    KHEADERS="/lib/modules/${KVER}/build"

    if [ ! -d "${KHEADERS}" ]; then
        echo "  Installing kernel headers..."
        apt-get install -y "linux-headers-${KVER}" 2>/dev/null || true
    fi

    if [ -d "${KHEADERS}" ]; then
        BUILD_DIR=$(mktemp -d)
        cd "${BUILD_DIR}"

        KERNEL_MAJOR=$(echo "${KVER}" | cut -d'.' -f1-2)
        echo "  Downloading WM8960 driver source (kernel ${KERNEL_MAJOR})..."
        wget -q "https://raw.githubusercontent.com/torvalds/linux/v${KERNEL_MAJOR}/sound/soc/codecs/wm8960.c" -O wm8960.c 2>/dev/null
        wget -q "https://raw.githubusercontent.com/torvalds/linux/v${KERNEL_MAJOR}/sound/soc/codecs/wm8960.h" -O wm8960.h 2>/dev/null

        if [ -f wm8960.c ] && [ -f wm8960.h ]; then
            printf 'obj-m += snd-soc-wm8960.o\nsnd-soc-wm8960-objs := wm8960.o\nKDIR := /lib/modules/$(shell uname -r)/build\nPWD := $(shell pwd)\nall:\n\tmake -C $(KDIR) M=$(PWD) modules\nclean:\n\tmake -C $(KDIR) M=$(PWD) clean\ninstall:\n\tmake -C $(KDIR) M=$(PWD) modules_install\n\tdepmod -a\n' > Makefile

            echo "  Compiling..."
            if make 2>&1 | tail -5; then
                echo "  Installing module..."
                make install 2>&1 | tail -3
                depmod -a
                WM8960_MODULE_AVAILABLE=true
                echo "  WM8960 kernel module compiled and installed successfully"
            else
                echo "  Warning: WM8960 kernel module compilation failed"
            fi
        else
            echo "  Warning: Failed to download WM8960 source code"
        fi

        cd /
        rm -rf "${BUILD_DIR}"
    else
        echo "  Warning: Kernel headers not found (${KHEADERS})"
        echo "  Please install: apt-get install linux-headers-${KVER}"
    fi
fi

if [ "$WM8960_MODULE_AVAILABLE" = true ]; then
    modprobe snd-soc-wm8960 2>/dev/null || true
    echo "  WM8960 kernel module loaded"
fi

# ==================== 6. Install ALSA Configuration ====================
echo ""
echo "[6/8] Installing ALSA configuration..."

if [ -f "${SCRIPT_DIR}/WM8960-Audio-HAT.zip" ]; then
    TMPDIR=$(mktemp -d)
    unzip -o "${SCRIPT_DIR}/WM8960-Audio-HAT.zip" -d "${TMPDIR}" > /dev/null 2>&1

    mkdir -p /etc/wm8960-soundcard
    cp "${TMPDIR}"/WM8960-Audio-HAT/*.conf /etc/wm8960-soundcard/ 2>/dev/null || true
    cp "${TMPDIR}"/WM8960-Audio-HAT/*.state /etc/wm8960-soundcard/ 2>/dev/null || true

    if [ -f "${TMPDIR}/WM8960-Audio-HAT/wm8960-soundcard" ]; then
        cp "${TMPDIR}/WM8960-Audio-HAT/wm8960-soundcard" /usr/bin/
        chmod u=rwx,go=rx /usr/bin/wm8960-soundcard
    fi
    if [ -f "${TMPDIR}/WM8960-Audio-HAT/wm8960-soundcard.service" ]; then
        cp "${TMPDIR}/WM8960-Audio-HAT/wm8960-soundcard.service" /lib/systemd/system/
        systemctl enable wm8960-soundcard.service 2>/dev/null || true
    fi

    rm -rf "${TMPDIR}"
    echo "  WM8960 ALSA configuration installed"
else
    echo "  WM8960-Audio-HAT.zip not found, skipping ALSA configuration"
fi

# ==================== 7. Configure ALSA Mixer ====================
echo ""
echo "[7/8] Configuring WM8960 ALSA mixer..."

# Set WM8960 as default sound card
cat > /etc/asound.conf << 'EOF'
defaults.pcm.card 1
defaults.pcm.device 0
defaults.ctl.card 1
EOF
echo "  WM8960 set as default sound card (/etc/asound.conf)"

# Enable DAC -> output mixer routing (WM8960 defaults to off, must be enabled manually)
# Use card name lookup instead of hardcoding -c 1
WM8960_CARD=$(cat /proc/asound/cards 2>/dev/null | grep -i wm8960 | head -1 | awk '{print $1}')
if [ -n "$WM8960_CARD" ]; then
    amixer -c "$WM8960_CARD" sset 'Left Output Mixer PCM' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Right Output Mixer PCM' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Speaker' 121 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Speaker DC' 5 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Speaker AC' 5 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Headphone' 120 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Playback' 230 2>/dev/null || true
    alsactl store "$WM8960_CARD" 2>/dev/null || true
    echo "  WM8960 mixer configured and saved (output routing enabled)"
else
    echo "  WM8960 sound card not detected, mixer config will take effect after reboot"
fi

# ==================== 8. Update U-Boot and Detect Hardware ====================
echo ""
echo "[8/8] Updating U-Boot boot config and detecting hardware..."

if command -v u-boot-update &>/dev/null; then
    u-boot-update
    echo "  U-Boot configuration updated"
else
    echo "  Warning: u-boot-update command not found"
    echo "  Please manually check the fdtoverlays line in /boot/extlinux/extlinux.conf"
fi

echo ""
echo "  Detecting hardware..."
if command -v i2cdetect &>/dev/null; then
    for bus in 0 3 6; do
        if [ -e "/dev/i2c-${bus}" ]; then
            result=$(i2cdetect -y "${bus}" 2>/dev/null | grep " 1a " || true)
            if [ -n "$result" ]; then
                echo "  ✓ WM8960 detected on I2C bus ${bus} (address 0x1a)"
            fi
        fi
    done
fi

if [ -e "/dev/spidev3.0" ]; then
    echo "  ✓ SPI3 device available (/dev/spidev3.0)"
else
    echo "  ✗ SPI3 device not found (reboot required)"
fi

echo ""
echo "=============================================="
echo " Installation Complete!"
echo "=============================================="
echo ""
echo "Please reboot to apply all changes:"
echo "  sudo reboot"
echo ""
echo "After reboot, run tests:"
echo "  cd $(dirname ${SCRIPT_DIR})/example"
echo "  sudo bash run_test.sh"
echo ""
echo "Verify sound card:"
echo "  aplay -l      # Should show wm8960-soundcard"
echo "  alsamixer      # Adjust WM8960 volume"
echo ""

if [ "$WM8960_MODULE_AVAILABLE" = false ]; then
    echo "Note: WM8960 audio driver kernel module could not be installed."
    echo "Audio features may require additional configuration. See:"
    echo "  https://docs.pisugar.com/docs/product-wiki/whisplay/intro"
    echo ""
fi
