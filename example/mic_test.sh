#!/bin/bash

# print aplay -l list output
aplay -l

# Find the sound card index for wm8960soundcard (playback)
playback_card_index=$(aplay -l | awk '/wm8960soundcard/ {gsub(/:/,"",$2); print $2; exit}')
# Default to 1 if not found
if [ -z "$playback_card_index" ]; then
  echo "Cannot not find wm8960soundcard for playback, defaulting to card index 1"
  playback_card_index=1
fi

echo "Using playback sound card index: $playback_card_index"

arecord -l

# Find the sound card index for wm8960soundcard (capture)
capture_card_index=$(arecord -l | awk '/wm8960soundcard/ {gsub(/:/,"",$2); print $2; exit}')
# Default to 0 if not found
if [ -z "$capture_card_index" ]; then
  echo "Cannot not find wm8960soundcard for capture, defaulting to card index 0"
  capture_card_index=0
fi

echo "Using capture sound card index: $capture_card_index"

# record audio from the microphone
echo "Recording 10 seconds of audio to mic_test.wav..."
arecord -D hw:$capture_card_index,0 -f cd -t wav -d 10 data/mic_test.wav
echo "Recording complete."
# play back the recorded audio
echo "Playing back the recorded audio..."
aplay -D hw:$playback_card_index,0 data/mic_test.wav
echo "Playback complete."
