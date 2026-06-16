#!/usr/bin/env bash
# Kill stuck aplay/arecord on whisplaysound (lab / calibration helper).
# Run on the Pi: sudo bash scripts/kill-stale-audio.sh

set -euo pipefail

pkill -f 'arecord.*hw:whisplaysound' 2>/dev/null || true
pkill -f 'aplay.*hw:whisplaysound' 2>/dev/null || true
pkill -f 'arecord.*whisplaysound' 2>/dev/null || true
pkill -f 'aplay.*whisplaysound' 2>/dev/null || true
pkill -f wp-lut 2>/dev/null || true
pkill -f wp-tone 2>/dev/null || true
echo "Stale Whisplay audio processes cleared."
