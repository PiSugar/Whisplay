#!/bin/sh
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
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
WHISPLAY_HAT_EEPROM_DETECTED=0
WHISPLAY_BOOT_OVERLAY_SOURCE=""
WHISPLAY_BOOT_CONFIG=""

dt_string() {
    local file="$1"
    [[ -r "$file" ]] || return 1
    tr -d '\0' <"$file" 2>/dev/null || true
}

whisplay_hat_eeprom_present() {
    local vendor=""
    local product_id=""

    vendor="$(dt_string /proc/device-tree/hat/vendor || true)"
    product_id="$(dt_string /proc/device-tree/hat/product_id || true)"

    [[ "${vendor,,}" == "pisugar" && "$product_id" == "0x0001" ]]
}

whisplay_soundcard_in_live_dt() {
    local compat=""

    compat="$(dt_string /proc/device-tree/sound/compatible || true)"
    [[ "$compat" == *"pisugar,whisplay-soundcard"* ]]
}

configure_boot_overlay() {
    local boot_cfg="$1"

    [[ "$PLATFORM" == "raspberry_pi" ]] || {
        echo "HAT EEPROM overlay handling is only supported on Raspberry Pi." >&2
        return 1
    }

    if whisplay_hat_eeprom_present; then
        WHISPLAY_HAT_EEPROM_DETECTED=1
    fi

    for param in i2c_arm=on i2s=on; do
        if ! grep -q "^dtparam=${param}" "$boot_cfg" 2>/dev/null; then
            echo "dtparam=${param}" >>"$boot_cfg"
        fi
    done

    if [[ "$WHISPLAY_HAT_EEPROM_DETECTED" == "1" && "${WHISPLAY_FORCE_CONFIG_OVERLAY:-0}" != "1" ]]; then
        sed -i '/^dtoverlay=whisplay-soundcard/d' "$boot_cfg" 2>/dev/null || true
        WHISPLAY_BOOT_OVERLAY_SOURCE="HAT EEPROM"
        echo "  Whisplay HAT EEPROM detected; leaving the sound-card overlay to EEPROM auto-loading."
        if ! whisplay_soundcard_in_live_dt; then
            echo "  WARN: The live DT has no Whisplay sound node yet; reboot after installation and re-check EEPROM overlay loading." >&2
        fi
        return 0
    fi

    if ! grep -q "^dtoverlay=whisplay-soundcard" "$boot_cfg" 2>/dev/null; then
        echo "dtoverlay=whisplay-soundcard" >>"$boot_cfg"
    fi
    WHISPLAY_BOOT_OVERLAY_SOURCE="config.txt"
}

detect_platform() {
    local model=""
    local compat=""
    local release=""
    local boot_env=""

    [[ -r /etc/orangepi-release ]] && release="$(cat /etc/orangepi-release 2>/dev/null || true)"
    [[ -r /boot/orangepiEnv.txt ]] && boot_env="$(cat /boot/orangepiEnv.txt 2>/dev/null || true)"

    if [[ -r /proc/device-tree/model ]]; then
        model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
    fi
    if [[ -r /proc/device-tree/compatible ]]; then
        compat="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null || true)"
    fi

    if echo "$release" | grep -qi '^BOARD=orangepizero3w$' || \
       echo "$boot_env" | grep -qi 'orangepi-zero3w\.dtb'; then
        echo "orangepi_zero3w"
        return 0
    fi
    if [[ "$model" == *"Raspberry Pi"* ]]; then
        echo "raspberry_pi"
        return 0
    fi
    if echo "$compat" | grep -qi "raspberrypi,bcm\|brcm,bcm\|raspberrypi"; then
        echo "raspberry_pi"
        return 0
    fi
    if [[ "$(uname -r 2>/dev/null || true)" == *"rpt-rpi"* ]] && \
            [[ -d /boot/firmware/overlays || -d /boot/overlays ]]; then
        echo "raspberry_pi"
        return 0
    fi
    if [[ "$model" == *"OrangePi Zero2 W"* ]] || \
       echo "$compat" | grep -qi "xunlong,orangepi-zero2w"; then
        echo "orangepi_zero2w"
        return 0
    fi
    if [[ "$model" == *"Cubie"* ]] || echo "$compat" | grep -qi "cubie-a7z"; then
        echo "radxa_cubie_a7z"
        return 0
    fi
    if [[ "$model" == *"Radxa"* ]] || echo "$compat" | grep -qi "radxa"; then
        echo "radxa_zero3w"
        return 0
    fi

    echo "unknown"
}

install_build_deps() {
    local platform_packages=()

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    if [[ "$PLATFORM" == "raspberry_pi" ]]; then
        if apt-get install -y -qq raspberrypi-kernel-headers device-tree-compiler \
                alsa-utils libasound2-plugins sox 2>/dev/null; then
            return
        fi
    fi

    if [[ "$PLATFORM" == "radxa_cubie_a7z" ]]; then
        platform_packages+=(kmod)
    fi

    if [[ "$PLATFORM" == "orangepi_zero2w" || \
          "$PLATFORM" == "orangepi_zero3w" ]]; then
        apt-get install -y -qq device-tree-compiler alsa-utils \
            libasound2-plugins sox wget xz-utils make gcc kmod
        return
    fi

    if apt-get install -y -qq "linux-headers-$(uname -r)" device-tree-compiler \
            alsa-utils libasound2-plugins sox "${platform_packages[@]}"; then
        return
    fi

    echo "  WARN: could not install kernel headers automatically." >&2
    echo "  Install headers manually, then re-run this script." >&2
}

ensure_orangepi_headers() {
    local archive
    local expected_sha256
    local headers_dir
    local headers_url
    local kver
    local package_headers_dir
    local unpack_dir
    local vendor_dir

    [[ "$PLATFORM" == "orangepi_zero2w" || \
       "$PLATFORM" == "orangepi_zero3w" ]] || return 0
    kver="$(uname -r)"
    if [[ "$PLATFORM" == "orangepi_zero3w" ]]; then
        headers_dir="/usr/src/linux-headers-6.6.98-whisplay-sun60iw2"
        if [[ "$(readlink -f "/lib/modules/${kver}/build" 2>/dev/null || true)" == "$headers_dir" &&
              -x "$headers_dir/scripts/basic/fixdep" &&
              -x "$headers_dir/scripts/mod/modpost" ]]; then
            return 0
        fi
    elif [[ -d "/lib/modules/${kver}/build" ]]; then
        return 0
    fi

    if [[ "$PLATFORM" == "orangepi_zero3w" ]]; then
        if [[ "$kver" != "6.6.98-sun60iw2" ]]; then
            echo "Pinned Orange Pi Zero 3W headers support 6.6.98-sun60iw2; found ${kver}." >&2
            echo "Install matching kernel headers and re-run this installer." >&2
            return 1
        fi

        archive="$(mktemp --suffix=.deb)"
        headers_dir="/usr/src/linux-headers-6.6.98-whisplay-sun60iw2"
        headers_url="https://apt.armbian.com/pool/main/l/linux-headers-vendor-sun60iw2/linux-headers-vendor-sun60iw2_26.8.3_arm64__6.6.98-S8a9b-D5397-P6965-C16f9-H213f-HKca97-Vc222-B4990-R448a.deb"
        expected_sha256="fe162a261159c307684a28e23c0140ea563a0804eddd3a7725033b962a1a8e66"

        echo "  Downloading compatible Linux 6.6.98 A733 headers ..."
        wget -q --show-progress -O "$archive" "$headers_url"
        echo "${expected_sha256}  ${archive}" | sha256sum -c -
        # Never extract this foreign distribution package directly over /.
        # Orange Pi OS uses merged-/usr (/lib -> usr/lib), while the Armbian
        # package contains a real top-level lib/ directory. Extracting it at /
        # would replace that symlink and make dynamically linked tools fail.
        unpack_dir="$(mktemp -d)"
        dpkg-deb -x "$archive" "$unpack_dir"
        rm -f "$archive"
        package_headers_dir="$unpack_dir/usr/src/linux-headers-6.6.98-vendor-sun60iw2"
        [[ -d "$package_headers_dir" ]] || {
            rm -rf "$unpack_dir"
            echo "Expected headers directory was not found in the package." >&2
            return 1
        }
        mkdir -p /usr/src
        rm -rf "$headers_dir"
        cp -a "$package_headers_dir" "$headers_dir"
        rm -rf "$unpack_dir"
        sed -i 's/6\.6\.98-vendor-sun60iw2/6.6.98-sun60iw2/g' \
            "$headers_dir/include/config/kernel.release" \
            "$headers_dir/include/generated/utsrelease.h"
        # The package omits a few arm64 generator inputs and all built host
        # tools. Generate only what external modules need; vendor
        # modules_prepare recurses indefinitely through BSP subdirectories.
        # Once the host tools exist, sync the official boot config so the
        # generated struct module ABI exactly matches the running kernel.
        vendor_dir="$SRC/vendor/linux-6.6"
        install -m 644 "$vendor_dir/gen-cpucaps.awk" \
            "$vendor_dir/cpucaps" "$vendor_dir/gen-sysreg.awk" \
            "$vendor_dir/sysreg" "$headers_dir/arch/arm64/tools/"
        mkdir -p "$headers_dir/arch/arm64/include/generated/asm"
        awk -f "$headers_dir/arch/arm64/tools/gen-cpucaps.awk" \
            "$headers_dir/arch/arm64/tools/cpucaps" \
            >"$headers_dir/arch/arm64/include/generated/asm/cpucaps.h"
        awk -f "$headers_dir/arch/arm64/tools/gen-sysreg.awk" \
            "$headers_dir/arch/arm64/tools/sysreg" \
            >"$headers_dir/arch/arm64/include/generated/asm/sysreg-defs.h"
        install -m 644 "$vendor_dir/devicetable-offsets-a733.h" \
            "$headers_dir/scripts/mod/devicetable-offsets.h"
        install -m 644 "$vendor_dir/elfconfig-a733.h" \
            "$headers_dir/scripts/mod/elfconfig.h"
        gcc -O2 -o "$headers_dir/scripts/basic/fixdep" \
            "$headers_dir/scripts/basic/fixdep.c"
        gcc -O2 -I "$headers_dir/scripts/mod" \
            -o "$headers_dir/scripts/mod/modpost" \
            "$headers_dir/scripts/mod/modpost.c" \
            "$headers_dir/scripts/mod/file2alias.c" \
            "$headers_dir/scripts/mod/sumversion.c" \
            "$headers_dir/scripts/mod/symsearch.c"
        cp "/boot/config-${kver}" "$headers_dir/.config"
        make -C "$headers_dir" KERNELRELEASE="$kver" olddefconfig
        make -C "$headers_dir" KERNELRELEASE="$kver" syncconfig
        mkdir -p "/lib/modules/${kver}"
        ln -sfn "$headers_dir" "/lib/modules/${kver}/build"
        echo "  Orange Pi Zero 3W kernel headers installed: $headers_dir"
        return 0
    fi

    if [[ "$kver" != "6.1.31-sun50iw9" ]]; then
        echo "Orange Pi OS headers are only available here for 6.1.31-sun50iw9; found ${kver}." >&2
        echo "Install matching kernel headers and re-run this installer." >&2
        return 1
    fi

    archive="$(mktemp)"
    headers_dir="/usr/src/kheaders-6.1.31-sun50iw9"
    headers_url="https://raw.githubusercontent.com/MJD19994/WM8960_AudioHAT_OrangePiZero_Drivers/58a1ea03d6efb6c59f66a291492517b26342a091/dkms/kheaders-6.1.31-sun50iw9.tar.xz"
    expected_sha256="82d0569483033d86e4335cce914bb6bae94ea57aeff5e233bcc8dd4c890f76da"

    echo "  Downloading matching Orange Pi OS 6.1.31 kernel headers ..."
    wget -q --show-progress -O "$archive" "$headers_url"
    echo "${expected_sha256}  ${archive}" | sha256sum -c -
    mkdir -p /usr/src
    rm -rf "$headers_dir"
    tar -xJf "$archive" -C /usr/src
    rm -f "$archive"
    ln -sfn "$headers_dir" "/lib/modules/${kver}/build"
    echo "  Orange Pi kernel headers installed: $headers_dir"
}

ensure_wm8960_codec() {
    local build_dir
    local headers
    local kernel_series
    local module_path

    [[ "$PLATFORM" == "radxa_cubie_a7z" || \
       "$PLATFORM" == "orangepi_zero2w" || \
       "$PLATFORM" == "orangepi_zero3w" ]] || return 0

    module_path="$(find "/lib/modules/$(uname -r)" -name 'snd-soc-wm8960.ko*' \
        -print -quit 2>/dev/null || true)"
    if [[ -n "$module_path" ]]; then
        depmod -a
        if modprobe snd-soc-wm8960 2>/dev/null; then
            echo "  WM8960 codec module available: $module_path"
            return 0
        fi
        echo "  Existing WM8960 module is ABI-incompatible; rebuilding it"
    fi

    headers="/lib/modules/$(uname -r)/build"
    if [[ ! -d "$headers" ]]; then
        echo "Missing kernel headers required to build the A733 WM8960 codec module." >&2
        return 1
    fi

    if ! command -v wget >/dev/null 2>&1; then
        apt-get install -y -qq wget
    fi

    build_dir="$(mktemp -d)"
    kernel_series="$(uname -r | cut -d. -f1-2)"
    echo "  Building WM8960 codec module for $PLATFORM (Linux $kernel_series) ..."

    if [[ "$PLATFORM" == "orangepi_zero2w" ]]; then
        local source_base
        source_base="https://raw.githubusercontent.com/MJD19994/WM8960_AudioHAT_OrangePiZero_Drivers/58a1ea03d6efb6c59f66a291492517b26342a091/dkms"
        wget -q -O "$build_dir/wm8960.c" "$source_base/wm8960.c"
        wget -q -O "$build_dir/wm8960.h" "$source_base/wm8960.h"
        echo 'fc7ce953f3af8709a45d53c8f486cbf3c6fd1d1f7dd7f72d0693e8546cb99b19  '"$build_dir/wm8960.c" | sha256sum -c -
        echo '31dbe5dc88d92aaae880633b50360aceff2e5035f20d9c6141ba618e6fe82859  '"$build_dir/wm8960.h" | sha256sum -c -
    elif [[ "$PLATFORM" == "orangepi_zero3w" ]]; then
        local vendor_dir
        vendor_dir="$SRC/vendor/linux-6.6"
        [[ -f "$vendor_dir/wm8960.c" && -f "$vendor_dir/wm8960.h" ]] || {
            rm -rf "$build_dir"
            echo "Bundled Linux 6.6 WM8960 codec source is missing." >&2
            return 1
        }
        cp "$vendor_dir/wm8960.c" "$build_dir/wm8960.c"
        cp "$vendor_dir/wm8960.h" "$build_dir/wm8960.h"
        echo 'e0c06f796e913992c6a98ff469fc0489dc05ebb6917a9eb8ea6def5bbf83db02  '"$build_dir/wm8960.c" | sha256sum -c -
        echo 'dc12f6e6a3ddc2b3729d1a3d307c6eec09ce3c592eb13f403c26cf58f8b1d2c1  '"$build_dir/wm8960.h" | sha256sum -c -
    elif ! wget -q \
          "https://raw.githubusercontent.com/torvalds/linux/v${kernel_series}/sound/soc/codecs/wm8960.c" \
          -O "$build_dir/wm8960.c" ||
         ! wget -q \
          "https://raw.githubusercontent.com/torvalds/linux/v${kernel_series}/sound/soc/codecs/wm8960.h" \
          -O "$build_dir/wm8960.h"; then
        rm -rf "$build_dir"
        echo "Failed to download the matching WM8960 codec source." >&2
        return 1
    fi

    printf '%s\n' \
        'obj-m += snd-soc-wm8960.o' \
        'snd-soc-wm8960-objs := wm8960.o' \
        >"$build_dir/Makefile"
    make -C "$headers" M="$build_dir" KBUILD_MODPOST_WARN=1 modules
    mkdir -p "/lib/modules/$(uname -r)/kernel/sound/soc/codecs"
    install -m 644 "$build_dir/snd-soc-wm8960.ko" \
        "/lib/modules/$(uname -r)/kernel/sound/soc/codecs/"
    rm -rf "$build_dir"
    depmod -a
    echo "  WM8960 codec module installed"
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
            WHISPLAY_BOOT_CONFIG="$boot_cfg"
            configure_boot_overlay "$boot_cfg"
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
        radxa_cubie_a7z)
            dts="$SRC/dts/whisplay-soundcard-radxa-cubie-a7z.dts"
            dtbo="/boot/dtbo/whisplay-soundcard-radxa-cubie-a7z.dtbo"
            mkdir -p /boot/dtbo
            dtc -I dts -O dtb -@ -o "$dtbo" "$dts"

            if [[ -f /boot/dtbo/sun60iw2p1-i2s0-2ch.dtbo ]]; then
                mv /boot/dtbo/sun60iw2p1-i2s0-2ch.dtbo \
                    /boot/dtbo/sun60iw2p1-i2s0-2ch.dtbo.disabled
                echo "  Disabled conflicting I2S0 dummy-sound overlay"
            fi
            if [[ -f /boot/dtbo/wm8960-cubie-a7z.dtbo ]]; then
                mv /boot/dtbo/wm8960-cubie-a7z.dtbo \
                    /boot/dtbo/wm8960-cubie-a7z.dtbo.disabled
                echo "  Disabled legacy Cubie A7Z WM8960 overlay"
            fi

            grep -q "i2c-dev" /etc/modules 2>/dev/null || echo "i2c-dev" >>/etc/modules
            grep -q "snd-soc-wm8960" /etc/modules 2>/dev/null || echo "snd-soc-wm8960" >>/etc/modules
            grep -q "snd-soc-whisplay-soundcard" /etc/modules 2>/dev/null || \
                echo "snd-soc-whisplay-soundcard" >>/etc/modules

            if command -v u-boot-update >/dev/null 2>&1; then
                u-boot-update
            else
                echo "  WARN: u-boot-update not found; verify /boot/extlinux/extlinux.conf manually." >&2
            fi
            ;;
        orangepi_zero2w)
            dts="$SRC/dts/whisplay-soundcard-orangepi-zero2w.dts"
            dtbo="/boot/overlay-user/whisplay-soundcard-orangepi-zero2w.dtbo"
            boot_cfg="/boot/orangepiEnv.txt"
            mkdir -p /boot/overlay-user
            dtc -I dts -O dtb -@ -o "$dtbo" "$dts"

            [[ -f "$boot_cfg" ]] || {
                echo "Missing Orange Pi boot environment: $boot_cfg" >&2
                exit 1
            }
            if grep -q '^user_overlays=' "$boot_cfg"; then
                if ! awk '$1 == "user_overlays" { for (i = 2; i <= NF; i++) if ($i == "whisplay-soundcard-orangepi-zero2w") found = 1 } END { exit !found }' FS='[= ]' "$boot_cfg"; then
                    sed -i '/^user_overlays=/ s/$/ whisplay-soundcard-orangepi-zero2w/' "$boot_cfg"
                fi
            else
                echo 'user_overlays=whisplay-soundcard-orangepi-zero2w' >>"$boot_cfg"
            fi
            grep -q '^i2c-dev$' /etc/modules 2>/dev/null || echo 'i2c-dev' >>/etc/modules
            grep -q '^snd-soc-whisplay-soundcard$' /etc/modules 2>/dev/null || \
                echo 'snd-soc-whisplay-soundcard' >>/etc/modules
            ;;
        orangepi_zero3w)
            dts="$SRC/dts/whisplay-soundcard-orangepi-zero3w.dts"
            dtbo="/boot/overlay-user/whisplay-soundcard-orangepi-zero3w.dtbo"
            boot_cfg="/boot/orangepiEnv.txt"
            mkdir -p /boot/overlay-user
            dtc -I dts -O dtb -@ -o "$dtbo" "$dts"

            [[ -f "$boot_cfg" ]] || {
                echo "Missing Orange Pi boot environment: $boot_cfg" >&2
                exit 1
            }
            if grep -q '^user_overlays=' "$boot_cfg"; then
                if ! awk '$1 == "user_overlays" { for (i = 2; i <= NF; i++) if ($i == "whisplay-soundcard-orangepi-zero3w") found = 1 } END { exit !found }' FS='[= ]' "$boot_cfg"; then
                    sed -i '/^user_overlays=/ s/$/ whisplay-soundcard-orangepi-zero3w/' "$boot_cfg"
                fi
            else
                echo 'user_overlays=whisplay-soundcard-orangepi-zero3w' >>"$boot_cfg"
            fi
            grep -q '^i2c-dev$' /etc/modules 2>/dev/null || echo 'i2c-dev' >>/etc/modules
            grep -q '^snd-soc-wm8960$' /etc/modules 2>/dev/null || echo 'snd-soc-wm8960' >>/etc/modules
            grep -q '^snd-soc-whisplay-soundcard$' /etc/modules 2>/dev/null || \
                echo 'snd-soc-whisplay-soundcard' >>/etc/modules
            ;;
        *)
            echo "Unsupported platform for overlay install: $PLATFORM" >&2
            exit 1
            ;;
    esac
}

install_a7z_recovery() {
    local recovery_script="/usr/local/sbin/whisplay-soundcard-a7z-recover"
    local recovery_service="/etc/systemd/system/whisplay-soundcard-a7z-recover.service"

    if [[ "$PLATFORM" != "radxa_cubie_a7z" ]]; then
        return 0
    fi

    install -m 755 "$ROOT/scripts/recover-a7z-i2c.sh" "$recovery_script"
    cat >"$recovery_service" <<'EOF'
[Unit]
Description=Whisplay Cubie A7Z TWI7 audio recovery
After=systemd-modules-load.service local-fs.target
Before=sound.target alsa-restore.service whisplay-soundcard-warmup.service whisplay-daemon.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/whisplay-soundcard-a7z-recover

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    if [[ "${WHISPLAY_A7Z_RECOVERY:-0}" == "1" ]]; then
        systemctl enable whisplay-soundcard-a7z-recover.service >/dev/null
        echo "  Cubie A7Z TWI7 boot recovery enabled"
    else
        systemctl disable --now whisplay-soundcard-a7z-recover.service \
            >/dev/null 2>&1 || true
        echo "  Cubie A7Z TWI7 recovery installed but disabled"
        echo "  Set WHISPLAY_A7Z_RECOVERY=1 to enable it explicitly"
    fi
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

echo "[1/8] Installing build dependencies ..."
install_build_deps

echo
echo "[2/8] Ensuring platform codec dependencies ..."
ensure_orangepi_headers
ensure_wm8960_codec

echo
echo "[3/8] Building snd-soc-whisplay-soundcard.ko ..."
if [[ "$PLATFORM" == "orangepi_zero2w" || \
      "$PLATFORM" == "orangepi_zero3w" ]]; then
    # The Zero 2W vendor archive has an empty Module.symvers; Zero 3W uses a
    # compatible A733 symvers from the pinned headers package. Keep modpost
    # warnings non-fatal for both vendor-kernel build paths.
    if [[ "$PLATFORM" == "orangepi_zero3w" ]]; then
        make -C "$SRC" clean
    fi
    make -C "$SRC" KBUILD_MODPOST_WARN=1
else
    make -C "$SRC"
fi

echo
echo "[4/8] Installing kernel module ..."
KVER="$(uname -r)"
mkdir -p "/lib/modules/${KVER}/kernel/sound/soc/codecs"
install -m 644 "$SRC/snd-soc-whisplay-soundcard.ko" \
    "/lib/modules/${KVER}/kernel/sound/soc/codecs/"
depmod -a

echo
echo "[5/8] Compiling and installing device-tree overlay ..."
install_overlay
install_a7z_recovery

sed -i '/snd-soc-wm8960-soundcard/d' /etc/modules 2>/dev/null || true
systemctl disable --now wm8960-soundcard.service >/dev/null 2>&1 || true
systemctl disable --now es8389-soundcard.service >/dev/null 2>&1 || true
systemctl disable --now es8389-defaults.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/sysinit.target.wants/wm8960-soundcard.service
rm -f /etc/systemd/system/sysinit.target.wants/es8389-soundcard.service
rm -f /etc/systemd/system/multi-user.target.wants/es8389-defaults.service
rm -f /etc/systemd/system/es8389-defaults.service
if [[ "$PLATFORM" == "radxa_cubie_a7z" || \
      "$PLATFORM" == "orangepi_zero3w" ]]; then
    systemctl disable --now wm8960-fix.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/wm8960-fix.service
    rm -f /usr/local/bin/wm8960-fix.sh
fi
rm -f /etc/wireplumber/main.lua.d/51-es8389.lua
rm -rf /etc/wm8960-soundcard /etc/es8389-soundcard
if [ -L /var/lib/alsa/asound.state ]; then
    case "$(readlink /var/lib/alsa/asound.state)" in
        *wm8960-soundcard*|*es8389-soundcard*) rm -f /var/lib/alsa/asound.state ;;
    esac
fi

echo
echo "[6/8] Installing ALSA configuration ..."
rm -f /etc/asound.conf
if [[ "$PLATFORM" == "radxa_cubie_a7z" ]]; then
    install -m 644 "$CFG/asound-a7z.conf" /etc/asound.conf
else
    install -m 644 "$CFG/asound.conf" /etc/asound.conf
fi
migrate_legacy_alsa_refs

echo
echo "[7/8] Module options ..."
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
echo "[8/8] Installing boot defaults ..."
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
if [[ "$PLATFORM" == "radxa_cubie_a7z" &&
      "${WHISPLAY_A7Z_WARMUP:-0}" != "1" ]]; then
    systemctl disable --now whisplay-soundcard-warmup.service \
        >/dev/null 2>&1 || true
    echo "  Cubie A7Z boot warmup installed but disabled"
    echo "  Set WHISPLAY_A7Z_WARMUP=1 to enable it explicitly"
else
    systemctl enable whisplay-soundcard-warmup.service >/dev/null
    echo "  Boot defaults enabled (speaker=80, mic=80)"
fi

echo
echo "===================================="
echo " Installation complete."
echo
if [[ "$PLATFORM" == "raspberry_pi" ]]; then
    if [[ "$WHISPLAY_HAT_EEPROM_DETECTED" == "1" ]]; then
        echo " Hardware: PiSugar Whisplay HAT EEPROM detected."
        if [[ "$WHISPLAY_BOOT_OVERLAY_SOURCE" == "HAT EEPROM" ]]; then
            echo " Overlay: whisplay-soundcard will be auto-loaded by the HAT EEPROM."
        else
            echo " Overlay: whisplay-soundcard is configured in $WHISPLAY_BOOT_CONFIG."
        fi
        echo
        echo " Note: if this OS image is later used with older Whisplay hardware"
        echo " without EEPROM, manually add this line to $WHISPLAY_BOOT_CONFIG:"
        echo "   dtoverlay=whisplay-soundcard"
    else
        echo " Hardware: no PiSugar Whisplay HAT EEPROM detected."
        echo " Overlay: whisplay-soundcard is configured in $WHISPLAY_BOOT_CONFIG."
    fi
fi
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
