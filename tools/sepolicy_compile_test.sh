#!/usr/bin/env bash
# Reproduce second-stage init's split-policy compile (system/core/init/selinux.cpp) on the build host.
#
#   sepolicy_compile_test.sh <system root> <vendor root> <odm root> [policy version] [strip-allowx]
#
# <system root> is the root of the system image (contains system/, system_ext/, product/).
# With "strip-allowx" the test emulates the TrebleDroid legacy-kernel path found in the ColorOS GSI init:
# the policy is capped to version 29 and only `allowx` rules are removed from the plat, system_ext, vendor
# and odm CIL files before compiling. Anything else that needs extended permissions then fails the compile,
# which on the phone is an init fatal error (reboot to recovery/bootloader).
set -euo pipefail
S=$1; V=$2; O=$3; VER=${4:-30}; MODE=${5:-}
SECILC=${SECILC:-secilc}
ver=$(tr -d '[:space:]' < "$V/etc/selinux/plat_sepolicy_vers.txt")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

src() {  # print the path to compile for a CIL file (optionally with allowx stripped)
  if [ "$MODE" = strip-allowx ]; then
    local out="$tmp/$(echo "$1" | tr / _)"
    grep -v '^(allowx ' "$1" > "$out" || true
    echo "$out"
  else
    echo "$1"
  fi
}

args=("$(src "$S/system/etc/selinux/plat_sepolicy.cil")" -m -M true -G -N -c "$VER"
      "$S/system/etc/selinux/mapping/$ver.cil")
[ -f "$S/system/etc/selinux/mapping/$ver.compat.cil" ] && args+=("$S/system/etc/selinux/mapping/$ver.compat.cil")
for part in system_ext product; do
  d="$S/$part/etc/selinux"
  [ -f "$d/${part}_sepolicy.cil" ] && args+=("$(src "$d/${part}_sepolicy.cil")")
  [ -f "$d/mapping/$ver.cil" ] && args+=("$d/mapping/$ver.cil")
done
args+=("$V/etc/selinux/plat_pub_versioned.cil" "$(src "$V/etc/selinux/vendor_sepolicy.cil")")
[ -s "$O/etc/selinux/odm_sepolicy.cil" ] && args+=("$(src "$O/etc/selinux/odm_sepolicy.cil")")
# Android 15+: genfs labels are versioned by the vendor image.
if [ -f "$V/etc/selinux/genfs_labels_version.txt" ]; then
  gv=$(tr -d '[:space:]' < "$V/etc/selinux/genfs_labels_version.txt")
  [ -f "$S/system/etc/selinux/plat_sepolicy_genfs_$gv.cil" ] && args+=("$S/system/etc/selinux/plat_sepolicy_genfs_$gv.cil")
fi

echo "vendor policy version $ver, compiling for kernel policy version $VER${MODE:+ ($MODE)}"
if "$SECILC" "${args[@]}" -o "$tmp/policy" -f /dev/null > "$tmp/log" 2>&1; then
  echo "COMPILE OK ($(stat -c%s "$tmp/policy") bytes)"
else
  echo "COMPILE FAILED"; tail -5 "$tmp/log"; exit 1
fi
