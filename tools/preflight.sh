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
# out_has <extended regex> <command...>: does the command's output match? The output is collected first:
# `command | grep -q` closes the pipe at the first match, and under pipefail a writer that is still writing
# then turns the match into a failure.
out_has() { local pat=$1 o; shift; o=$("$@" 2>/dev/null) || true; grep -Eq -- "$pat" <<< "$o"; }

PR=$WORK/mnt/prt
cleanup() { for d in "$PR" "$PS" "$PV" "$PO"; do mountpoint -q "$d" && umount "$d"; done; rm -f "$WORK/pf.log" "$WORK/pf-runtime.img"; }
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

echo "-- vendor HALs against the ColorOS system libraries"
mkdir -p "$PR"
unzip -p "$PS/system/apex/com.android.runtime.apex" apex_payload.img > "$WORK/pf-runtime.img" && mount -o ro,loop "$WORK/pf-runtime.img" "$PR" \
  || bad "cannot mount ColorOS's runtime APEX payload"
if $PY "$T/vendor_link_check.py" "$PV" "$PO" "$PS" "$H/base-llndk" "$PR" > "$WORK/pf.log" 2>&1; then
  ok "$(head -1 "$WORK/pf.log"): $(tail -1 "$WORK/pf.log")"
  grep '^  note:' "$WORK/pf.log" | sed 's/^  note:/        note:/'
else
  bad "vendor code cannot link against the ColorOS system"; sed 's/^/        /' "$WORK/pf.log" | tail -12
fi

echo "-- mounts: first-stage (boot ramdisk) and second-stage (vendor) fstab"
b=$WORK/pf-boot; rm -rf "$b"; mkdir -p "$b"
$PY "$T/bootimg.py" unpack "$OUT/boot.img" "$b" > "$b/info" 2>&1
gzip -dc "$b/ramdisk" > "$b/rd.cpio" && $PY "$T/cpio_edit.py" get "$b/rd.cpio" system/etc/fstab.qcom "$b/fstab"
cmp -s "$b/fstab" "$PV/etc/fstab.qcom" && ok "ramdisk fstab and vendor fstab are identical" || bad "ramdisk fstab differs from the vendor fstab"
sysl=$(awk '!/^#/ && $2=="/system" {print $3}' "$b/fstab" | tr '\n' ' ')
[ "$sysl" = "erofs ext4 " ] && ok "/system: erofs entry first, ext4 fallback kept" || bad "/system entries: $sysl"
out_has 'logical,first_stage_mount' awk '!/^#/ && $2=="/system" && $3=="erofs"' "$b/fstab" && ok "/system erofs entry is logical + first_stage_mount" || bad "/system erofs entry flags"
for p in vendor odm; do out_has first_stage_mount awk -v p="/$p" '!/^#/ && $2==p && $3=="erofs"' "$b/fstab" && ok "/$p is mounted as erofs in the first stage" || bad "/$p fstab entry"; done
if [ "${FBE_MODE:-standard}" = standard ]; then
  d=$(awk '!/^#/ && $2=="/data"' "$b/fstab")
  grep -q 'fileencryption=aes-256-xts:aes-256-cts:v2,' <<< "$d" && ! grep -Eq 'wrappedkey|inlinecrypt|metadata_encryption|keydirectory' <<< "$d" \
    && ok "/data: standard file-based encryption, no wrapped keys, no metadata encryption" || bad "/data encryption options: $d"
  grep -q 'formattable' <<< "$d" && ok "/data is formattable (a blank userdata is formatted on first boot)" || bad "/data not formattable"
fi
for mp in $(awk '!/^#/ && NF>=5 && $2 ~ /^\/(system_ext|product)$/ {print $2}' "$b/fstab"); do
  grep -qx "$mp" "$PS/system_ext/etc/init/config/skip_mount.cfg" && ok "$mp (not in this super) is skipped by the GSI's skip_mount.cfg" || bad "$mp is in the fstab but not skipped"
done
grep -q 'androidboot.super_partition=system' "$b/info" && ok "kernel cmdline still names the retrofit super device" || bad "cmdline lost androidboot.super_partition"
rm -rf "$b"

echo "-- eBPF loader override (Android 17 network stack on the 4.14 kernel)"
rc=$PS/system/etc/init/00-port-netbpfload.rc
[ "$(grep -c '^    mount none ' "$rc")" = 9 ] && ok "9 bind mounts in 00-port-netbpfload.rc" || bad "bind mount count in $rc"
first=$(cd "$PS/system/etc/init" && ls | LC_ALL=C sort | grep -n -x -e 00-port-netbpfload.rc -e netbpfload.rc | awk -F: 'NR == 1 { print $2 }')
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

if [ "${IMS:-base}" = base ]; then
  echo "-- IMS app (VoLTE): the base ROM's app, patched, in place of ColorOS's"
  ia=$PS/system_ext/priv-app/ims
  out_has "'v2', 'v3'" $PY "$T/apk_sigblock.py" info "$ia/ims.apk" && zipalign -c 4 "$ia/ims.apk" >/dev/null 2>&1 \
    && ok "ims.apk carries a v2/v3 signing block and is 4-byte aligned" || bad "ims.apk signing block or alignment"
  [ "$(aapt2 dump packagename "$ia/ims.apk" 2>/dev/null)" = org.codeaurora.ims ] && ok "ims.apk parses as org.codeaurora.ims" || bad "ims.apk does not parse"
  python3 -I -c "
import sys, zipfile
a, b = zipfile.ZipFile(sys.argv[1]), zipfile.ZipFile(sys.argv[2])
assert set(a.namelist()) == set(b.namelist())
assert [n for n in a.namelist() if a.read(n) != b.read(n)] == ['classes.dex']
" "$H/ims/ims-base.apk" "$ia/ims.apk" 2>/dev/null && ok "ims.apk differs from the base ROM's app only in classes.dex" || bad "ims.apk content"
  if $PY "$T/dex_link_check.py" "$ia/ims.apk" "${IMS_LIBS:?}" --strict \
       --base /system="$H/base-platform/system" /system_ext="$H/base-platform/system_ext" /product="$H/base-platform/product" \
       --target /system="$PS/system" /system_ext="$PS/system_ext" /product="$PS/product" > "$WORK/pf.log" 2>&1; then
    ok "ims.apk: $(sed -n 1p "$WORK/pf.log"); all resolve on ColorOS's class path, no constructor left dangling"
  else
    bad "ims.apk references something ColorOS's class path does not have"; sed 's/^/        /' "$WORK/pf.log" | tail -8
  fi
  [ ! -e "$ia/oat" ] && ok "no stale compiled code of ColorOS's app" || bad "ims/oat still present"
  for f in libimsmedia_jni.so libimscamera_jni.so; do
    [ -f "$ia/lib/arm64/$f" ] && [ ! -L "$ia/lib/arm64/$f" ] && cmp -s "$ia/lib/arm64/$f" "$H/ims/$f" \
      && [ "$(lab "$ia/lib/arm64/$f")" = "u:object_r:system_lib_file:s0" ] && ok "$f: the base ROM's library, labelled system_lib_file" || bad "$f"
  done
  python3 -I - "$ia/lib/arm64" "$PS/system/lib64" "$PR/lib64/bionic" <<'PY' && ok "the IMS JNI libraries resolve their imports in ColorOS's platform libraries" || bad "IMS JNI library imports"
import os, subprocess, sys
def syms(p, kind):
    out = subprocess.run(["nm", "-D", f"--{kind}-only", p], capture_output=True, text=True).stdout
    return {l.split()[-1].split("@")[0] for l in out.splitlines() if l.strip()}
bad = False
for lib in ("libimsmedia_jni.so", "libimscamera_jni.so"):
    p = os.path.join(sys.argv[1], lib)
    need = [l.split("[")[1].split("]")[0] for l in subprocess.run(["readelf", "-d", p], capture_output=True, text=True).stdout.splitlines() if "(NEEDED)" in l]
    provided = set()
    for n in need:
        for d in sys.argv[2:4]:
            q = os.path.join(d, n)
            if os.path.isfile(q) and not os.path.islink(q):
                provided |= syms(q, "defined")
                break
        else:
            if n != "libnativehelper.so":        # lives in the ART APEX; its jni* helpers are accepted below
                print(lib, "needs", n, "which ColorOS does not provide"); bad = True
    missing = {s for s in syms(p, "undefined") if s not in provided and not s.startswith("jni")}
    if missing:
        print(lib, "unresolved:", sorted(missing)[:5]); bad = True
    if "JNI_OnLoad" not in syms(p, "defined"):
        print(lib, "has no JNI_OnLoad"); bad = True
sys.exit(1 if bad else 0)
PY
fi

echo "-- services the vendor image does not have: nothing may wait for them without a timeout"
m=$PS/system/etc/vintf/manifest.xml
for h in vendor.oplus.hardware.oplusSensor vendor.oplus.hardware.urcc vendor.oplus.hardware.hummingbird; do
  if grep -rqs "<name>$h</name>" "$PV/etc/vintf" "$PO/etc/vintf"; then ok "$h is served by the vendor image"
  elif grep -q "<name>$h</name>" "$m"; then bad "$h is declared in the framework manifest but nothing serves it"
  else ok "$h: not declared, so clients skip it instead of waiting"; fi
done
u=$PS/system_ext/lib64/libupdateprof.qti.so
check "libupdateprof.qti.so: the original with one import swapped (waitForService -> checkService)" $PY "$T/elf_swap_import.py" verify \
  "$H/libupdateprof.orig" "$u" AServiceManager_waitForService AServiceManager_checkService LIBBINDER_NDK
out_has ' AServiceManager_checkService@@LIBBINDER_NDK$' readelf --dyn-syms -W "$PS/system/lib64/libbinder_ndk.so" \
  && ! out_has 'waitForService' readelf --dyn-syms -W "$u" && [ "$(lab "$u")" = "$(lab "$PS/system_ext/lib64/libtrigger-handler.so")" ] \
  && ok "ColorOS's libbinder_ndk.so exports the lookup the library now imports; label unchanged" || bad "libupdateprof.qti.so import or label"
f=$PS/system/bin/surfaceflinger
if $PY "$T/patch_sf_waits.py" verify "$H/surfaceflinger.orig" "$f" \
     "$PS/system/etc/vintf" "$PS/system_ext/etc/vintf" "$PS/product/etc/vintf" "$PV/etc/vintf" "$PO/etc/vintf" > "$WORK/pf.log" 2>&1; then
  ok "surfaceflinger: the original with $(cut -d: -f1 "$WORK/pf.log") to the non-blocking lookup, nothing else changed"
  sed 's/^[^:]*: //; s/; /\n/g' "$WORK/pf.log" | sort | uniq -c | sed 's/^ *\([0-9]*\) /        \1x /'
else
  bad "surfaceflinger wait redirection"; sed 's/^/        /' "$WORK/pf.log" | tail -4
fi
[ "$(lab "$f")" = "u:object_r:surfaceflinger_exec:s0" ] && [ -x "$f" ] && ok "surfaceflinger keeps its label and mode" || bad "surfaceflinger label/mode: $(lab "$f")"
! grep -qsx 'ro.vendor.qspm.enable=true' "$PV/build.prop" "$PV/default.prop" "$PO/etc/build.prop" "$PS/system/build.prop" "$PS"/*/build.prop "$PS"/*/etc/build.prop \
  && ok "ro.vendor.qspm.enable is not set: libtrigger-handler.so stays off" || bad "ro.vendor.qspm.enable=true without a QSPM HAL"
! grep -q 'oplus.gpu.controlpanel.support' "$PS"/my_product/etc/permissions/*.xml && ok "GPU control panel feature not declared" || bad "GPU control panel feature still declared"
grep -qx 'ro.adb.secure=0' "$PS/system/build.prop" && ok "ro.adb.secure=0 (with 1, system_server waits for the Oplus engineer HAL)" || bad "ro.adb.secure is not 0"

echo "-- scheduler groups (schedtune on the 4.14 kernel)"
if $PY - "$PS" "$PV" > "$WORK/pf.log" 2>&1 <<'PY'
import json, re, sys
from pathlib import Path
s, v = Path(sys.argv[1]), Path(sys.argv[2])
levels = {re.search(r"^ro\.product\.first_api_level=(\d+)$", (p / "build.prop").read_text(errors="replace"), re.M).group(1)
          for p in (v, s / "my_manifest")}
main_cg = json.loads((s / "system/etc/cgroups.json").read_text())
main_tp = json.loads((s / "system/etc/task_profiles.json").read_text())
rc = (s / "system/etc/init/00-port-stune.rc").read_text()
assert rc.splitlines()[1] == "on init" and "\r" not in rc
groups = set(re.findall(r"^    mkdir /dev/stune/(\S+)$", rc, re.M))
for g in groups:
    for f in ("tasks", "cgroup.procs"):
        assert f"    chown system system /dev/stune/{g}/{f}\n" in rc and f"    chmod 0664 /dev/stune/{g}/{f}\n" in rc, (g, f)
n_join = 0
for api in sorted(levels):
    cg = json.loads((s / f"system/etc/task_profiles/cgroups_{api}.json").read_text())
    assert [(c["Controller"], c["Path"]) for c in cg["Cgroups"]] == [("schedtune", "/dev/stune")], cg
    known = {c["Controller"] for c in main_cg["Cgroups"]} | {"schedtune"} \
        | {c["Controller"] for c in main_cg.get("Cgroups2", {}).get("Controllers", [])}
    tp = json.loads((s / f"system/etc/task_profiles/task_profiles_{api}.json").read_text())
    attrs = {a["Name"] for a in main_tp.get("Attributes", []) + tp.get("Attributes", [])}
    assert all(a["Controller"] in known for a in tp.get("Attributes", []))
    names = {p["Name"] for p in main_tp["Profiles"] + tp["Profiles"]}
    for p in tp["Profiles"]:
        for a in p["Actions"]:
            prm = a.get("Params", {})
            if a["Name"] == "JoinCgroup":
                assert prm["Controller"] in known, (p["Name"], prm)
                if prm["Controller"] == "schedtune":
                    assert prm["Path"] in groups | {""}, (p["Name"], prm)
                    n_join += 1
            elif a["Name"] == "SetAttribute":
                assert prm["Name"] in attrs, (p["Name"], prm)
    for agg in tp.get("AggregateProfiles", []):
        assert set(agg["Profiles"]) <= names, agg
    assert n_join, "no profile joins a schedtune group"
want = {"top-app", "foreground", "background"}
assert want <= groups, groups
print(f"API levels {','.join(sorted(levels))}: schedtune mounted at /dev/stune, groups {','.join(sorted(groups))}, profiles consistent")
PY
then ok "$(cat "$WORK/pf.log")"; else bad "scheduler group files"; sed 's/^/        /' "$WORK/pf.log" | tail -6; fi
api=$(sed -n 's/^ro.product.first_api_level=//p' "$PV/build.prop")
[ "$(lab "$PS/system/etc/task_profiles/cgroups_$api.json")" = "u:object_r:cgroup_desc_file:s0" ] \
  && [ "$(lab "$PS/system/etc/task_profiles/task_profiles_$api.json")" = "u:object_r:task_profiles_file:s0" ] \
  && [ "$(lab "$PS/system/etc/init/00-port-stune.rc")" = "u:object_r:system_file:s0" ] && ok "scheduler files carry the labels the policy gives those paths" || bad "scheduler file labels"
grep -q '/etc/task_profiles/cgroups_%u.json' "$PS/system/lib64/libprocessgroup_setup.so" && grep -q '/etc/task_profiles/task_profiles_%u.json' "$PS/system/lib64/libprocessgroup.so" \
  && ok "ColorOS's libprocessgroup reads API-level cgroup and task-profile files" || bad "libprocessgroup has no API-level file support"

echo "-- display"
c=$PV/bin/hw/android.hardware.graphics.composer@2.4-service
d=$(cmp -l "$H/composer.orig" "$c" | wc -l)
[ "$d" = 4 ] && [ "$(cmp -l "$H/composer.orig" "$c" | awk '{printf "%s ", $3}')" = "37 40 3 325 " ] \
  && ok "composer service: exactly one instruction changed to NOP (unknown commands are skipped)" || bad "composer patch ($d bytes differ)"
[ "$(lab "$c")" = "$(lab "$WORK/vendor/bin/hw/android.hardware.graphics.composer@2.4-service")" ] && [ -x "$c" ] && ok "composer service keeps its label and mode" || bad "composer label/mode"
for f in android_framework_res_overlay.display.product.24831.apk oplus_framework_res_overlay.display.product.24831.apk; do
  out_has 'jar verified' jarsigner -verify "$PS/my_product/overlay/$f" && zipalign -c 4 "$PS/my_product/overlay/$f" >/dev/null 2>&1 \
    && ok "$f: signed and aligned" || bad "$f"
done
res=$(aapt2 dump resources "$PS/my_product/overlay/android_framework_res_overlay.display.product.24831.apk" 2>/dev/null)
awk '/config_stableDeviceDisplayHeight/ { getline; if ($0 ~ /2340/) found = 1 } END { exit !found }' <<< "$res" \
  && ok "display overlay describes the 1080x2340 panel" || bad "display overlay values"

echo "-- properties and configuration"
grep -qx 'debug.stagefright.ccodec=4' "$PS/system/build.prop" && ok "Codec2 for all codecs" || bad "ccodec"
grep -qx 'persist.vendor.radio.poweron_opt=0' "$PS/system_ext/etc/build.prop" && ok "qcrild SMS buffering off" || bad "poweron_opt"
grep -q 'export CUST_LEVEL_LIST ' "$PS/init.environ.rc" && ok "CUST_LEVEL_LIST exported" || bad "CUST_LEVEL_LIST"
grep -qx libcamera_metadata.so "$PS/system/etc/llndk.libraries.txt" && ok "libcamera_metadata.so is LLNDK" || bad "llndk list"
[ ! -e "$PS/system_ext/etc/init/supersize.rc" ] && grep -qx 'ro.oplus.storage.super_size=6442450944' "$PS/system_ext/etc/build.prop" && ok "super size property fixed" || bad "super size"
python3 -I -c "import sys, xml.dom.minidom as m; [m.parse(f) for f in sys.argv[1:]]" "$PS/my_product/etc/power_profile/power_profile.xml" \
  "$PS"/my_product/etc/extension/com.oplus.oplus-feature.xml "$PS"/my_product/etc/extension/com.oplus.app-features.xml \
  "$PS"/my_product/etc/extension/feature_com.android.battery.xml "$PV/etc/vintf/manifest.xml" \
  "$PS/system/etc/vintf/manifest.xml" "$PS/my_product/etc/permissions/oplus.product.features_gameeco_common.xml" 2>/dev/null \
  && ok "edited XML files are well-formed" || bad "an edited XML file does not parse"
if [ "${SOUNDTRIGGER:-remove}" = remove ]; then
  ! grep -q 'android.hardware.soundtrigger' "$PV/etc/vintf/manifest.xml" && ! out_has 'soundtrigger@' ls "$PV"/lib*/hw \
    && ok "SoundTrigger HIDL adapter and manifest entry removed together" || bad "SoundTrigger removal incomplete"
fi
# Load order is system (+ my_* imports), system_ext, vendor, odm, product; the last definition wins.
den=$(cat "$PS/system/build.prop" "$PS"/my_*/build.prop "$PS/system_ext/etc/build.prop" "$PV/build.prop" "$PO/etc/build.prop" "$PS/product/etc/build.prop" 2>/dev/null \
      | sed -n 's/^ro.sf.lcd_density=//p' | tail -1)
[ "$den" = "$(sed -n 's/^ro.oplus.density.fhd_default=//p' "$PS/my_product/build.prop")" ] && [ "$den" = 480 ] \
  && ok "effective display density is 480, ColorOS's default for a 1080-wide panel (the vendor's 440 is overridden)" || bad "effective ro.sf.lcd_density is '$den'"
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
