#!/bin/bash

# print aplay -l list output
aplay -l

# Find the unified Whisplay sound card first; keep legacy names as fallback.
card_info=$(awk '/whisplaysound|wm8960soundcard|es8389soundcard/ {print $1 " " $2; exit}' /proc/asound/cards)
card_index=$(echo "$card_info" | awk '{print $1}')
card_name=$(echo "$card_info" | awk '{print $2}' | tr -d '[]:')
if [ -n "$card_index" ]; then
  echo "Using sound card: ${card_name:-unknown} (index $card_index)"
else
  echo "Whisplay sound card not found; using default ALSA device"
fi

if [ -n "$card_index" ]; then
  if [ "$card_name" = "whisplaysound" ]; then
    AUDIODEV=whisplaysound python3 test.py "$@"
  else
    AUDIODEV="plughw:CARD=${card_name}" python3 test.py "$@"
  fi
else
  python3 test.py "$@"
fi
