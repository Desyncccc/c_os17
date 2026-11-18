#!/usr/bin/env bash
# Off-device checks of the built images. Every check corresponds to a failure that is fatal at boot and that
# cannot be diagnosed on the phone without a log (init fatal error -> reboot to recovery/bootloader), or to
# an invariant the port relies on. The images are checked as built (loop-mounted), not the work trees.
#
#   WORK=<work dir> OUT=<out dir> preflight.sh
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$ROOT/tools
PY="python3 -I"
: "${WORK:?}" "${OUT:?}"
PS=$WORK/mnt/psys; PV=$WORK/mnt/pvend; PO=$WORK/mnt/podm
H=$WORK/harvest
fail=0
ok()   { echo "  ok    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
check() { local desc=$1; shift; if "$@" > "$WORK/pf.log" 2>&1; then ok "$desc"; else bad "$desc"; sed 's/^/        /' "$WORK/pf.log" | tail -6; fi; }
lab()  { getfattr -h -n security.selinux --only-values --absolute-names "$1" 2>/dev/null | tr -d '\0'; }

cleanup() { for d in "$PS" "$PV" "$PO"; do mountpoint -q "$d" && umount "$d"; done; rm -f "$WORK/pf.log"; }
trap cleanup EXIT
mkdir -p "$PS" "$PV" "$PO"
mount -t erofs -o ro,loop "$OUT/system.img" "$PS" && mount -t erofs -o ro,loop "$OUT/vendor.img" "$PV" \
  && mount -t erofs -o ro,loop "$OUT/odm.img" "$PO" || { echo "cannot mount the built images"; exit 1; }

echo "-- images mount with the host kernel's EROFS driver and carry the tree's metadata"
[ "$(lab "$PS/system/bin/init")" = "u:object_r:init_exec:s0" ] && ok "system: init label" || bad "system: init label is '$(lab "$PS/system/bin/init")'"
[ "$(lab "$PV/etc/fstab.qcom")" = "u:object_r:vendor_configs_file:s0" ] && ok "vendor: fstab label" || bad "vendor: fstab label"
[ "$(stat -c '%u:%g %a' "$PS/system/bin/init")" = "0:2000 755" ] && ok "system: init owner/mode" || bad "system: init owner/mode $(stat -c '%u:%g %a' "$PS/system/bin/init")"
getfattr -h -n security.capability --absolute-names "$PS/system/bin/run-as" >/dev/null 2>&1 && ok "system: file capabilities kept (run-as)" || bad "system: run-as lost its file capability"
n=$(cd "$PS" && find . -xdev \( -type f -o -type d -o -type l \) | wc -l); m=$(cd "$WORK/sys" && find . -xdev \( -type f -o -type d -o -type l \) | wc -l)
[ "$n" = "$m" ] && ok "system: $n entries, same as the tree" || bad "system: image has $n entries, tree has $m"
n=$(cd "$PV" && find . -xdev \( -type f -o -type d -o -type l \) | wc -l); m=$(cd "$WORK/vendor" && find . -xdev \( -type f -o -type d -o -type l \) | wc -l)
[ "$n" = "$m" ] && ok "vendor: $n entries, same as the tree" || bad "vendor: image has $n entries, tree has $m"
u=$(cd "$PS" && find . -xdev \( -type f -o -type d \) -exec sh -c 'getfattr -h -n security.selinux --absolute-names "$1" >/dev/null 2>&1 || echo "$1"' _ {} \; | wc -l)
[ "$u" = 0 ] && ok "system: every file and directory has a SELinux label" || bad "system: $u entries without a SELinux label"
u=$(cd "$PV" && find . -xdev \( -type f -o -type d \) -exec sh -c 'getfattr -h -n security.selinux --absolute-names "$1" >/dev/null 2>&1 || echo "$1"' _ {} \; | wc -l)
[ "$u" = 0 ] && ok "vendor: every file and directory has a SELinux label" || bad "vendor: $u entries without a SELinux label"

echo "-- SELinux: init's split-policy compile (system + system_ext + product + vendor + odm)"
check "policy compiles for kernel policy version 31 (what the 4.14 kernel supports)" bash "$T/sepolicy_compile_test.sh" "$PS" "$PV" "$PO" 31
check "policy compiles for version 30" bash "$T/sepolicy_compile_test.sh" "$PS" "$PV" "$PO" 30
check "policy compiles the way the GSI's legacy-kernel path does it (version 29, allowx stripped)" bash "$T/sepolicy_compile_test.sh" "$PS" "$PV" "$PO" 29 strip-allowx
check "no type/attribute declared on both sides" $PY "$T/selinux_port.py" conflicts "$PS" "$PV" "$PO"
check "property_contexts merge into one trie (no name defined twice)" $PY "$T/selinux_port.py" propctx "$PS" "$PV" "$PO"
check "service/hwservice/keystore2 contexts do not clash" $PY "$T/selinux_port.py" contexts "$PS" "$PV" "$PO"
$PY - "$PS" "$PV" "$PO" <<'PY' && ok "no property name appears in more than one property_contexts file" || bad "duplicate property_contexts names"
import sys, collections
from pathlib import Path
s, v, o = map(Path, sys.argv[1:4])
files = [s/"system/etc/selinux/plat_property_contexts", s/"system_ext/etc/selinux/system_ext_property_contexts",
         s/"product/etc/selinux/product_property_contexts", v/"etc/selinux/vendor_property_contexts",
         o/"etc/selinux/odm_property_contexts"]
seen = collections.Counter()
for f in files:
    if f.exists():
        for line in f.read_text(errors="replace").splitlines():
            p = line.split("#", 1)[0].split()
            if len(p) >= 2:
                seen[(p[0], p[2] if len(p) > 2 else "prefix")] += 1
dup = [k for k, n in seen.items() if n > 1]
print(dup[:10]) if dup else None
sys.exit(1 if dup else 0)
PY

echo "-- mounts: first-stage (boot ramdisk) and second-stage (vendor) fstab"
b=$WORK/pf-boot; rm -rf "$b"; mkdir -p "$b"
$PY "$T/bootimg.py" unpack "$OUT/boot.img" "$b" > "$b/info" 2>&1
gzip -dc "$b/ramdisk" > "$b/rd.cpio" && $PY "$T/cpio_edit.py" get "$b/rd.cpio" system/etc/fstab.qcom "$b/fstab"
cmp -s "$b/fstab" "$PV/etc/fstab.qcom" && ok "ramdisk fstab and vendor fstab are identical" || bad "ramdisk fstab differs from the vendor fstab"
sysl=$(awk '!/^#/ && $2=="/system" {print $3}' "$b/fstab" | tr '\n' ' ')
[ "$sysl" = "erofs ext4 " ] && ok "/system: erofs entry first, ext4 fallback kept" || bad "/system entries: $sysl"
awk '!/^#/ && $2=="/system" && $3=="erofs"' "$b/fstab" | grep -q 'logical,first_stage_mount' && ok "/system erofs entry is logical + first_stage_mount" || bad "/system erofs entry flags"
for p in vendor odm; do awk -v p="/$p" '!/^#/ && $2==p && $3=="erofs"' "$b/fstab" | grep -q first_stage_mount && ok "/$p is mounted as erofs in the first stage" || bad "/$p fstab entry"; done
if [ "${FBE_MODE:-standard}" = standard ]; then
  d=$(awk '!/^#/ && $2=="/data"' "$b/fstab")
  echo "$d" | grep -q 'fileencryption=aes-256-xts:aes-256-cts:v2,' && ! echo "$d" | grep -Eq 'wrappedkey|inlinecrypt|metadata_encryption|keydirectory' \
    && ok "/data: standard file-based encryption, no wrapped keys, no metadata encryption" || bad "/data encryption options: $d"
  echo "$d" | grep -q 'formattable' && ok "/data is formattable (a blank userdata is formatted on first boot)" || bad "/data not formattable"
fi
for mp in $(awk '!/^#/ && NF>=5 && $2 ~ /^\/(system_ext|product)$/ {print $2}' "$b/fstab"); do
  grep -qx "$mp" "$PS/system_ext/etc/init/config/skip_mount.cfg" && ok "$mp (not in this super) is skipped by the GSI's skip_mount.cfg" || bad "$mp is in the fstab but not skipped"
done
grep -q 'androidboot.super_partition=system' "$b/info" && ok "kernel cmdline still names the retrofit super device" || bad "cmdline lost androidboot.super_partition"
rm -rf "$b"

echo "-- eBPF loader override (Android 17 network stack on the 4.14 kernel)"
rc=$PS/system/etc/init/00-port-netbpfload.rc
[ "$(grep -c '^    mount none ' "$rc")" = 9 ] && ok "9 bind mounts in 00-port-netbpfload.rc" || bad "bind mount count in $rc"
first=$(cd "$PS/system/etc/init" && ls | LC_ALL=C sort | grep -n -x -e 00-port-netbpfload.rc -e netbpfload.rc | head -1 | cut -d: -f2)
[ "$first" = 00-port-netbpfload.rc ] && ok "the override rc is parsed before netbpfload.rc" || bad "rc ordering"
while read -r _ _ src dst _; do
  [ -e "$PS$src" ] || bad "bind source $src missing"
  case $src in */bpf) ;; *) grep -q ro.bpf.kver_override "$PS$src" || bad "$src is not override-aware";; esac
done < <(grep '^    mount none ' "$rc")
[ "$(lab "$PS/system/etc/port-tether/bin/netbpfload")" = "u:object_r:bpfloader_exec:s0" ] && ok "netbpfload replacement has the bpfloader_exec label" || bad "netbpfload label"
kv=$(sed -n 's/^ro.bpf.kver_override=//p' "$PS/system/build.prop"); [ -n "$kv" ] && [ "$kv" = "$(cat "$H/kver")" ] && ok "ro.bpf.kver_override=$kv (the base ROM's value)" || bad "ro.bpf.kver_override"
unzip -p "$PS/system/apex/com.android.tethering.apex" apex_manifest.pb >/dev/null 2>&1 && ok "ColorOS's tethering APEX itself is untouched and readable" || bad "tethering apex"

echo "-- port services"
for rc in "$PS"/system/etc/init/00-port-*.rc; do
  while read -r _ name bin _; do
    [ -x "$PS$bin" ] && ok "$(basename "$rc"): service $name -> $bin exists" || bad "$(basename "$rc"): $bin missing or not executable"
  done < <(grep '^service ' "$rc")
  grep -q $'\r' "$rc" && bad "$(basename "$rc") has CRLF line endings"
  [ "$(lab "$rc")" = "u:object_r:system_file:s0" ] || bad "$(basename "$rc") label"
done
python3 -I -c "
import struct, sys
d = open(sys.argv[1], 'rb').read()
assert d[:4] == b'dex\n' and struct.unpack_from('<I', d, 0x60)[0] == 2 and b'Lport/OplusStubs;' in d
" "$PS/system/framework/oplus-port-stubs.dex" 2>/dev/null && ok "Oplus stand-in dex holds only the two port classes" || bad "oplus-port-stubs.dex"
grep -q '^(type su)' "$PS/system/etc/selinux/plat_sepolicy.cil" && ok "su domain exists for the port services' seclabel" || bad "no su domain"

echo "-- display"
c=$PV/bin/hw/android.hardware.graphics.composer@2.4-service
d=$(cmp -l "$H/composer.orig" "$c" | wc -l)
[ "$d" = 4 ] && [ "$(cmp -l "$H/composer.orig" "$c" | awk '{printf "%s ", $3}')" = "37 40 3 325 " ] \
  && ok "composer service: exactly one instruction changed to NOP (unknown commands are skipped)" || bad "composer patch ($d bytes differ)"
[ "$(lab "$c")" = "$(lab "$WORK/vendor/bin/hw/android.hardware.graphics.composer@2.4-service")" ] && [ -x "$c" ] && ok "composer service keeps its label and mode" || bad "composer label/mode"
for f in android_framework_res_overlay.display.product.24831.apk oplus_framework_res_overlay.display.product.24831.apk; do
  jarsigner -verify "$PS/my_product/overlay/$f" 2>/dev/null | grep -q 'jar verified' && zipalign -c 4 "$PS/my_product/overlay/$f" >/dev/null 2>&1 \
    && ok "$f: signed and aligned" || bad "$f"
done
aapt2 dump resources "$PS/my_product/overlay/android_framework_res_overlay.display.product.24831.apk" 2>/dev/null | grep -A1 config_stableDeviceDisplayHeight | grep -q 2340 \
  && ok "display overlay describes the 1080x2340 panel" || bad "display overlay values"

echo "-- properties and configuration"
grep -qx 'debug.stagefright.ccodec=4' "$PS/system/build.prop" && ok "Codec2 for all codecs" || bad "ccodec"
grep -qx 'persist.vendor.radio.poweron_opt=0' "$PS/system_ext/etc/build.prop" && ok "qcrild SMS buffering off" || bad "poweron_opt"
grep -q 'export CUST_LEVEL_LIST ' "$PS/init.environ.rc" && ok "CUST_LEVEL_LIST exported" || bad "CUST_LEVEL_LIST"
grep -qx libcamera_metadata.so "$PS/system/etc/llndk.libraries.txt" && ok "libcamera_metadata.so is LLNDK" || bad "llndk list"
[ ! -e "$PS/system_ext/etc/init/supersize.rc" ] && grep -qx 'ro.oplus.storage.super_size=6442450944' "$PS/system_ext/etc/build.prop" && ok "super size property fixed" || bad "super size"
python3 -I -c "import sys, xml.dom.minidom as m; [m.parse(f) for f in sys.argv[1:]]" "$PS/my_product/etc/power_profile/power_profile.xml" \
  "$PS"/my_product/etc/extension/com.oplus.oplus-feature.xml "$PS"/my_product/etc/extension/com.oplus.app-features.xml \
  "$PS"/my_product/etc/extension/feature_com.android.battery.xml "$PV/etc/vintf/manifest.xml" 2>/dev/null \
  && ok "edited XML files are well-formed" || bad "an edited XML file does not parse"
if [ "${SOUNDTRIGGER:-remove}" = remove ]; then
  ! grep -q 'android.hardware.soundtrigger' "$PV/etc/vintf/manifest.xml" && ! ls "$PV"/lib*/hw 2>/dev/null | grep -q 'soundtrigger@' \
    && ok "SoundTrigger HIDL adapter and manifest entry removed together" || bad "SoundTrigger removal incomplete"
fi
$PY - "$PS" <<'PY' && ok "no property is defined twice within one edited build.prop" || bad "duplicate property introduced"
import sys, collections
from pathlib import Path
bad = False
for rel in ("system/build.prop", "system_ext/etc/build.prop"):
    c = collections.Counter(l.split("=", 1)[0] for l in (Path(sys.argv[1]) / rel).read_text(errors="replace").splitlines()
                            if "=" in l and not l.startswith("#"))
    for k in ("ro.bpf.kver_override", "debug.stagefright.ccodec", "ro.oplus.storage.super_size", "persist.vendor.radio.poweron_opt"):
        if c[k] > 1:
            print(rel, k, c[k]); bad = True
sys.exit(1 if bad else 0)
PY

echo
[ "$fail" = 0 ] && echo "PREFLIGHT PASS" || echo "PREFLIGHT FAIL"
exit $fail
