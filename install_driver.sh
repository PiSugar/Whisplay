#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

detect_platform() {
  local model=""
  local compat=""
  if [[ -r /proc/device-tree/model ]]; then
    model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
  fi
  if [[ -r /proc/device-tree/compatible ]]; then
    compat="$(tr '\0' '\n' < /proc/device-tree/compatible 2>/dev/null || true)"
  fi

  if [[ "$model" == *"Raspberry Pi"* ]]; then
    echo "raspberry_pi"
    return 0
  fi
  if echo "$compat" | grep -qi "cubie-a7z"; then
    echo "radxa_cubie_a7z"
    return 0
  fi
  if [[ "$model" == *"Radxa"* ]] || echo "$compat" | grep -qi "radxa"; then
    echo "radxa_zero3w"
    return 0
  fi
  return 1
}

platform="$(detect_platform || true)"
case "$platform" in
  raspberry_pi)
    exec bash "$SCRIPT_DIR/script/install_raspberry_pi.sh" "$@"
    ;;
  radxa_zero3w)
    exec bash "$SCRIPT_DIR/script/install_radxa_zero3w.sh" "$@"
    ;;
  radxa_cubie_a7z)
    exec bash "$SCRIPT_DIR/script/install_radxa_cubie_a7z.sh" "$@"
    ;;
  *)
    echo "Unsupported or unknown platform."
    echo "Use one of these manually if needed:"
    echo "  script/install_raspberry_pi.sh"
    echo "  script/install_radxa_zero3w.sh"
    echo "  script/install_radxa_cubie_a7z.sh"
    exit 1
    ;;
esac
