#!/usr/bin/env bash
# Flash the ColorOS 17 port to a Xiaomi Mi 9 (cepheus) from the bootloader's fastboot mode.
# Run from the unpacked package directory:  ./flash_all.sh
#
# Writes boot, dtbo, vbmeta, system and vendor (and cust if the package has super_cust.img),
# and ERASES userdata and metadata.
#
#   ./flash_all.sh --keep-data   re-flash a newer build of this port over itself without erasing
#                                (only valid when the installed build used the same encryption mode)
# Does not touch the bootloader, modem or any other firmware partition.
set -euo pipefail
cd "$(dirname "$0")"
FB=${FASTBOOT:-fastboot}
KEEP_DATA=0
[ "${1:-}" = --keep-data ] && KEEP_DATA=1
command -v "$FB" >/dev/null || { echo "fastboot not found (install Android platform-tools)"; exit 1; }
for f in boot.img dtbo.img vbmeta.img super_system.img super_vendor.img; do
  [ -f "$f" ] || { echo "missing $f"; exit 1; }
done
if command -v sha256sum >/dev/null; then sha256sum -c --quiet SHA256SUMS || { echo "checksum mismatch: download again"; exit 1; }; fi

product=$("$FB" getvar product 2>&1 | sed -n 's/^product: *//p')
[ "$product" = cepheus ] || { echo "this device reports product '$product', not cepheus. Aborting."; exit 1; }
unlocked=$("$FB" getvar unlocked 2>&1 | sed -n 's/^unlocked: *//p')
[ "$unlocked" != no ] || { echo "the bootloader is locked. Unlock it first."; exit 1; }

if [ "$KEEP_DATA" = 1 ]; then
  echo; echo "Re-flashing the port WITHOUT erasing userdata (use only over an earlier build of this port)."; echo
else
  cat <<'MSG'

This will install an EXPERIMENTAL, never device-tested ColorOS 17 port and ERASE ALL USER DATA
(internal storage included). Requirements: Android 17 retrofit base firmware (Project Infinity-X 4.x
or another retrofit dynamic-partition ROM was installed before), a backup of everything you need.

MSG
fi
read -r -p "Type YES to continue: " answer
[ "$answer" = YES ] || { echo "aborted"; exit 1; }

"$FB" flash boot boot.img
"$FB" flash dtbo dtbo.img
"$FB" flash vbmeta vbmeta.img
"$FB" flash system super_system.img
"$FB" flash vendor super_vendor.img
# cust is the third member of the retrofit super; it is only written when the build needs its space.
if [ -f super_cust.img ]; then "$FB" flash cust super_cust.img; fi
# metadata holds state tied to the previous installation (key directories, lock-screen slots); a factory
# reset clears it too. First-stage init formats it again on the next boot.
if [ "$KEEP_DATA" = 0 ]; then
  "$FB" erase metadata || echo "warning: could not erase metadata (continuing)"
  # frp keeps the previous ROM's screen-lock handle; left in place, ColorOS refuses to set a new PIN.
  "$FB" erase frp || echo "warning: could not erase frp (continuing)"
  "$FB" erase userdata
fi
"$FB" reboot
echo "Done. The first boot formats userdata and can take 5-10 minutes. See README.txt if it does not boot."
