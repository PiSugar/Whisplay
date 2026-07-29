#!/usr/bin/env bash
#
# Recover Cubie A7Z TWI7 after its early-boot probe timing failure.
# The A733 vendor TWI driver can come up before the Whisplay codec responds.
# Rebinding TWI7 recreates both codec clients and lets the already-loaded
# WM8960/Whisplay drivers probe again. The same path works for ES8389.

set -u

CARD_PATTERN='whisplaysound'
TWI_DEVICE='2517000.twi'
TWI_DRIVER='/sys/bus/platform/drivers/sunxi-twi'

card_ready() {
    grep -qi "$CARD_PATTERN" /proc/asound/cards 2>/dev/null
}

modprobe snd_soc_wm8960 2>/dev/null || true
modprobe snd_soc_whisplay_soundcard 2>/dev/null || true

if card_ready; then
    echo "Whisplay sound card already available"
    exit 0
fi

for attempt in 1 2 3 4 5; do
    echo "A7Z TWI7 recovery attempt ${attempt}/5"

    if [[ -e "${TWI_DRIVER}/${TWI_DEVICE}" ]]; then
        echo "$TWI_DEVICE" >"${TWI_DRIVER}/unbind" 2>/dev/null || true
        sleep 1
    fi

    if [[ -e "${TWI_DRIVER}/bind" ]]; then
        echo "$TWI_DEVICE" >"${TWI_DRIVER}/bind" 2>/dev/null || true
    fi
    sleep 2

    if card_ready; then
        echo "Whisplay sound card recovered on attempt ${attempt}"
        exit 0
    fi
done

echo "WARN: Whisplay codec did not respond after TWI7 recovery attempts" >&2
exit 0
