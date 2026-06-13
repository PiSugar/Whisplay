#!/bin/bash

# print aplay -l list output
aplay -l

# Find the unified Whisplay sound card first; keep legacy names as fallback.
card_index=$(awk '/whisplaysound|wm8960soundcard|es8389soundcard/ {print $1}' /proc/asound/cards | head -n1)
if [ -n "$card_index" ]; then
  echo "Using sound card index: $card_index"
else
  echo "Whisplay sound card not found; using default ALSA device"
fi

if [ -n "$card_index" ]; then
  AUDIODEV=hw:$card_index,0 python3 test.py "$@"
else
  python3 test.py "$@"
fi
