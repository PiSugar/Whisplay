# Linux 6.6 build inputs

These pinned GPL-2.0 Linux build inputs support external-module builds on the
official Orange Pi Zero 3W Debian 1.0.0 image (`6.6.98-sun60iw2`). The image
does not ship kernel headers, and the compatible A733 headers package omits
several generated host-build inputs.

- `wm8960.c` and `wm8960.h`: Linux v6.6, `sound/soc/codecs/`
- `cpucaps`, `gen-cpucaps.awk`, `sysreg`, and `gen-sysreg.awk`: Linux v6.6,
  `arch/arm64/tools/`
- `devicetable-offsets-a733.h` and `elfconfig-a733.h`: generated on AArch64
  from Linux 6.6.98 with the official Orange Pi Zero 3W kernel configuration

The installer verifies the downloaded headers package, prepares a private
headers directory, regenerates the ARM64 headers, builds `fixdep`/`modpost`,
and runs `olddefconfig` plus `syncconfig` against the board's `/boot/config-*`.
It never extracts the foreign headers package over the target root filesystem.
