#!/bin/bash
#
# Whisplay HAT Driver Install Script - Radxa Cubie A7Z (Allwinner A733)
# Features: Enable SPI1/TWI7/I2S0 overlays, install Python dependencies,
#           compile and install WM8960 audio driver kernel module, configure sound card
#

set -e

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 1>&2
   exit 1
fi

# Detect if running on Radxa Cubie A7Z
COMPAT=$(cat /proc/device-tree/compatible 2>/dev/null | tr '\0' '\n' || true)
is_CubieA7Z=$(echo "$COMPAT" | grep -i "cubie-a7z" || true)
if [ -z "${is_CubieA7Z}" ]; then
  echo "Error: This script is only for Radxa Cubie A7Z"
  echo "Detected compatible: ${COMPAT}"
  echo "For Radxa ZERO 3W, use install_radxa_zero3w.sh"
  echo "For Raspberry Pi, use install_wm8960_drive.sh"
  exit 1
fi

MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo "Radxa Cubie A7Z")

echo "================================================="
echo " Whisplay HAT Driver Install - Radxa Cubie A7Z"
echo "================================================="
echo ""
echo "Detected platform: ${MODEL} (${COMPAT})"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DTBO_DIR="/boot/dtbo"

# ==================== 1. Install System Dependencies ====================
echo "[1/8] Installing system dependencies..."
apt-get update -y
apt-get install -y \
    linux-headers-$(uname -r) \
    python3-dev \
    python3-pip \
    build-essential \
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
    wget \
    kmod

echo "  System dependencies installed"

# ==================== 2. Enable SPI1 Overlay (LCD display) ====================
echo ""
echo "[2/8] Enabling SPI1 Overlay (for LCD display)..."

SPI_OVERLAY="sun60iw2p1-spi1-spidev.dtbo"
SPI_OVERLAY_DISABLED="${SPI_OVERLAY}.disabled"

if [ -f "${DTBO_DIR}/${SPI_OVERLAY}" ]; then
    echo "  SPI1 Overlay already enabled"
elif [ -f "${DTBO_DIR}/${SPI_OVERLAY_DISABLED}" ]; then
    cp "${DTBO_DIR}/${SPI_OVERLAY_DISABLED}" "${DTBO_DIR}/${SPI_OVERLAY}"
    echo "  SPI1 Overlay enabled: ${SPI_OVERLAY}"
else
    echo "  Warning: SPI1 Overlay file not found"
    echo "  Please enable manually: rsetup -> Overlay -> Enable ${SPI_OVERLAY}"
fi

# ==================== 3. Enable TWI7 Overlay (I2C for WM8960) ====================
echo ""
echo "[3/8] Enabling TWI7 Overlay (for 40-pin header Pin3/5 I2C)..."

# Radxa Cubie A7Z 40-pin header Pin3(SDA)/Pin5(SCL) maps to TWI7 (PJ22/PJ23)
TWI_OVERLAY="sun60iw2p1-twi7.dtbo"
TWI_OVERLAY_DISABLED="${TWI_OVERLAY}.disabled"

if [ -f "${DTBO_DIR}/${TWI_OVERLAY}" ]; then
    echo "  TWI7 Overlay already enabled"
elif [ -f "${DTBO_DIR}/${TWI_OVERLAY_DISABLED}" ]; then
    cp "${DTBO_DIR}/${TWI_OVERLAY_DISABLED}" "${DTBO_DIR}/${TWI_OVERLAY}"
    echo "  TWI7 Overlay enabled: ${TWI_OVERLAY}"
else
    echo "  Warning: TWI7 Overlay file not found"
    echo "  Please enable manually: rsetup -> Overlay -> Enable ${TWI_OVERLAY}"
fi

# ==================== 4. Configure WM8960 Audio Overlay ====================
echo ""
echo "[4/8] Configuring WM8960 audio overlay..."

# Disable I2S0 dummy-sound overlay (conflicts with WM8960 overlay)
I2S_DUMMY_OVERLAY="sun60iw2p1-i2s0-2ch.dtbo"
if [ -f "${DTBO_DIR}/${I2S_DUMMY_OVERLAY}" ]; then
    mv "${DTBO_DIR}/${I2S_DUMMY_OVERLAY}" "${DTBO_DIR}/${I2S_DUMMY_OVERLAY}.disabled"
    echo "  Disabled I2S0 dummy-sound overlay (WM8960 overlay configures I2S0 itself)"
else
    echo "  I2S0 dummy-sound overlay already disabled"
fi

# Compile WM8960 device tree overlay
echo "  Compiling WM8960 device tree overlay..."
WM8960_DTS="${SCRIPT_DIR}/wm8960-cubie-a7z.dts"

if [ -f "${WM8960_DTS}" ]; then
    WM8960_DTBO="${DTBO_DIR}/wm8960-cubie-a7z.dtbo"
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
    # Check if module file exists but modinfo fails
    KVER=$(uname -r)
    WM8960_KO=$(find /lib/modules/${KVER} -name 'snd-soc-wm8960.ko*' 2>/dev/null | head -1)
    if [ -n "$WM8960_KO" ]; then
        depmod -a
        WM8960_MODULE_AVAILABLE=true
        echo "  Found existing module at ${WM8960_KO}, ran depmod"
    fi
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
WM8960_CARD=$(cat /proc/asound/cards 2>/dev/null | grep -i wm8960 | head -1 | awk '{print $1}')
if [ -n "$WM8960_CARD" ]; then
    cat > /etc/asound.conf << EOF
defaults.pcm.card ${WM8960_CARD}
defaults.pcm.device 0
defaults.ctl.card ${WM8960_CARD}
EOF
    echo "  WM8960 set as default sound card (card ${WM8960_CARD})"

    # --- Capture path: enable input boost mixer -> PGA -> ADC ---
    # These switches connect the mic signal to the ADC (critical for recording!)
    amixer -c "$WM8960_CARD" cset name='Left Input Mixer Boost Switch' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" cset name='Right Input Mixer Boost Switch' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" cset name='Left Boost Mixer LINPUT1 Switch' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" cset name='Right Boost Mixer RINPUT1 Switch' on 2>/dev/null || true
    # Input gain: +20dB mic preamp boost
    amixer -c "$WM8960_CARD" cset name='Left Input Boost Mixer LINPUT1 Volume' 2 2>/dev/null || true
    amixer -c "$WM8960_CARD" cset name='Right Input Boost Mixer RINPUT1 Volume' 2 2>/dev/null || true
    # PGA capture volume
    amixer -c "$WM8960_CARD" cset name='Capture Volume' 50,50 2>/dev/null || true
    amixer -c "$WM8960_CARD" cset name='ADC PCM Capture Volume' 215,215 2>/dev/null || true
    # ADC High Pass Filter to remove DC offset at startup
    amixer -c "$WM8960_CARD" cset name='ADC High Pass Filter Switch' on 2>/dev/null || true
    echo "  Capture path configured (mic boost + PGA + ADC HPF)"

    # --- Playback path: DAC -> output mixer -> speaker ---
    amixer -c "$WM8960_CARD" sset 'Left Output Mixer PCM' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Right Output Mixer PCM' on 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Speaker' 121 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Speaker DC' 5 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Speaker AC' 5 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Headphone' 120 2>/dev/null || true
    amixer -c "$WM8960_CARD" sset 'Playback' 235 2>/dev/null || true
    echo "  Playback path configured (DAC -> mixer -> speaker)"

    alsactl store "$WM8960_CARD" 2>/dev/null || true
    echo "  ALSA mixer state saved"
else
    echo "  WM8960 sound card not detected yet"
    echo "  Default ALSA config will use card 0 (takes effect after reboot)"
    cat > /etc/asound.conf << 'EOF'
defaults.pcm.card 0
defaults.pcm.device 0
defaults.ctl.card 0
EOF
fi

# --- Install WM8960 boot fix service ---
# Workaround for TWI7 I2C bus timing issue: WM8960 may fail to probe at boot.
# This service rebinds the TWI7 driver and reconfigures ALSA mixer on boot.
echo "  Installing WM8960 boot fix service..."

cat > /usr/local/bin/wm8960-fix.sh << 'FIXEOF'
#!/bin/bash
# WM8960 boot fix: rebind TWI7 to work around I2C probe timing issue
# on Radxa Cubie A7Z (Allwinner A733)
set -e

TWI_DRIVER=/sys/bus/platform/drivers/sunxi-twi
TWI_DEV=2517000.twi
MODULE=snd_soc_wm8960
CARD_NAME=wm8960soundcard

# Check if WM8960 sound card already exists
if cat /proc/asound/cards 2>/dev/null | grep -q "$CARD_NAME"; then
    echo "WM8960 sound card already present, no fix needed"
    # Still configure mixer in case state was lost
else
    echo "WM8960 sound card not found, rebinding TWI7..."

    # Unbind TWI7
    if [ -e "${TWI_DRIVER}/${TWI_DEV}" ]; then
        echo "${TWI_DEV}" > "${TWI_DRIVER}/unbind" 2>/dev/null || true
        sleep 1
    fi

    # Rebind TWI7
    echo "${TWI_DEV}" > "${TWI_DRIVER}/bind"
    sleep 1

    # Reload WM8960 codec module
    modprobe -r ${MODULE} 2>/dev/null || true
    sleep 0.5
    modprobe ${MODULE}
    sleep 1

    # Verify
    if cat /proc/asound/cards 2>/dev/null | grep -q "$CARD_NAME"; then
        echo "WM8960 sound card successfully recovered"
    else
        echo "WARNING: WM8960 still not detected after rebind" >&2
        exit 1
    fi
fi

# Configure ALSA mixer
CARD_NUM=$(cat /proc/asound/cards | grep "$CARD_NAME" | head -1 | awk '{print $1}')
if [ -n "$CARD_NUM" ]; then
    # Capture path
    amixer -c "$CARD_NUM" cset name='Left Input Mixer Boost Switch' on 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Right Input Mixer Boost Switch' on 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Left Boost Mixer LINPUT1 Switch' on 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Right Boost Mixer RINPUT1 Switch' on 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Left Input Boost Mixer LINPUT1 Volume' 2 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Right Input Boost Mixer RINPUT1 Volume' 2 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Capture Volume' 50,50 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='ADC PCM Capture Volume' 215,215 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='ADC High Pass Filter Switch' on 2>/dev/null || true
    # Playback path
    amixer -c "$CARD_NUM" cset name='Left Output Mixer PCM Playback Switch' on 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Right Output Mixer PCM Playback Switch' on 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Speaker Playback Volume' 121,121 2>/dev/null || true
    amixer -c "$CARD_NUM" cset name='Playback Volume' 235,235 2>/dev/null || true
    echo "ALSA mixer configured for card $CARD_NUM"
fi

alsactl store 2>/dev/null || true
FIXEOF

chmod +x /usr/local/bin/wm8960-fix.sh

cat > /etc/systemd/system/wm8960-fix.service << 'SVCEOF'
[Unit]
Description=WM8960 I2C/TWI7 rebind workaround for Whisplay HAT
After=local-fs.target
Before=sound.target alsa-restore.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/wm8960-fix.sh

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable wm8960-fix.service 2>/dev/null || true
echo "  WM8960 boot fix service installed and enabled"

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
    for bus in $(ls /dev/i2c-* 2>/dev/null | sed 's|/dev/i2c-||'); do
        result=$(i2cdetect -y "${bus}" 2>/dev/null | grep " 1a " || true)
        if [ -n "$result" ]; then
            echo "  ✓ WM8960 detected on I2C bus ${bus} (address 0x1a)"
        fi
    done
fi

if [ -e "/dev/spidev1.0" ]; then
    echo "  ✓ SPI1 device available (/dev/spidev1.0)"
else
    echo "  ✗ SPI1 device not found (reboot required)"
fi

echo ""
echo "================================================="
echo " Installation Complete!"
echo "================================================="
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
echo "IMPORTANT - Audio sample rate:"
echo "  The A733 I2S0 clock (pll-audio1) only supports 48kHz family rates"
echo "  (48000, 32000, 16000, 8000 Hz). Do NOT use 44100 Hz — it causes"
echo "  crackling/popping due to clock inaccuracy."
echo ""
echo "  Recording example (use -r 48000, NOT -f cd):"
echo "    arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -t wav -d 5 test.wav"
echo "    aplay -D hw:0,0 test.wav"
echo ""

if [ "$WM8960_MODULE_AVAILABLE" = false ]; then
    echo "Note: WM8960 audio driver kernel module could not be installed."
    echo "Audio features may require additional configuration. See:"
    echo "  https://docs.pisugar.com/docs/product-wiki/whisplay/intro"
    echo ""
fi
