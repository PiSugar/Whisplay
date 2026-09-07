#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none
export UCF_FORCE_CONFOLD=1

repo_dir="/home/pi/Whisplay"
repo_url="${WHISPLAY_REPO:-https://github.com/PiSugar/Whisplay.git}"
repo_ref="${WHISPLAY_REF:-main}"
tmpdir="$(mktemp -d)"
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT

if [ ! -d "$repo_dir/.git" ]; then
  mkdir -p /home/pi
  git clone --depth 1 --branch "$repo_ref" "$repo_url" "$repo_dir"
else
  git -C "$repo_dir" fetch --depth 1 origin "$repo_ref"
  git -C "$repo_dir" checkout --force FETCH_HEAD
fi
chown -R pi:pi "$repo_dir"

installer="$repo_dir/audio/whisplay-soundcard/scripts/install.sh"
if [ ! -f "$installer" ]; then
  echo "Whisplay unified sound card installer not found: $installer" >&2
  exit 1
fi

fakebin="$tmpdir/bin"
mkdir -p "$fakebin"
cat > "$fakebin/uname" <<'EOF'
#!/bin/bash
if [ "$#" -eq 1 ] && [ "$1" = "-r" ] && [ -n "${WHISPLAY_TARGET_KVER:-}" ]; then
  printf '%s\n' "$WHISPLAY_TARGET_KVER"
  exit 0
fi
exec /usr/bin/uname "$@"
EOF
chmod 0755 "$fakebin/uname"

target_kernels=()
while IFS= read -r target_kver; do
  target_kernels+=("$target_kver")
done < <(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -V)

if [ "${#target_kernels[@]}" -eq 0 ]; then
  echo "No kernel module directories found under /lib/modules" >&2
  exit 1
fi

# pi-gen runs in a chroot whose uname belongs to the Docker host. Build and
# register the module once for every kernel shipped in the target image.
sed -i 's/^depmod -a$/depmod -a "$KVER"/' "$installer"
for target_kver in "${target_kernels[@]}"; do
  echo "Installing Whisplay sound card driver for kernel ${target_kver}"
  WHISPLAY_TARGET_KVER="$target_kver" PATH="$fakebin:$PATH" bash "$installer"
done
