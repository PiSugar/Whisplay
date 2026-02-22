#!/bin/bash
#
# Whisplay Recording & Playback Demo Launch Script
# Auto-detect WM8960 sound card and configure environment
#

# Find WM8960 sound card number
card_index=$(cat /proc/asound/cards 2>/dev/null | grep -i wm8960 | head -1 | awk '{print $1}')
if [ -z "$card_index" ]; then
    echo "WM8960 sound card not detected, using default card 1"
    card_index=1
fi
echo "WM8960 sound card number: $card_index"

# Ensure recording input is enabled
amixer -c "$card_index" sset 'Left Input Mixer Boost' on  2>/dev/null
amixer -c "$card_index" sset 'Right Input Mixer Boost' on 2>/dev/null
amixer -c "$card_index" sset 'Capture' 50                 2>/dev/null

# Ensure output routing is enabled
amixer -c "$card_index" sset 'Left Output Mixer PCM' on   2>/dev/null
amixer -c "$card_index" sset 'Right Output Mixer PCM' on  2>/dev/null
amixer -c "$card_index" sset 'Speaker' 121                2>/dev/null
amixer -c "$card_index" sset 'Playback' 230               2>/dev/null

cd "$(dirname "$0")"
AUDIODEV="hw:$card_index,0" python3 record_play_demo.py --card "$card_index" "$@"
