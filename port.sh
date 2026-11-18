#!/usr/bin/env bash
# ColorOS 17 for the Xiaomi Mi 9 (cepheus): build the flashable images from the two public inputs.
#
#   sudo ./port.sh                 run every stage
#   sudo ./port.sh system vendor   run only the named stages (see STAGES below)
#
# Environment: WORK (scratch dir, ~25 GB free), OUT (results), plus the options in config/cepheus.env.
# Must run as root: the system and vendor trees are copied with their owners, modes and security.* xattrs
# (SELinux labels, file capabilities) and those are what mkfs.erofs writes into the images.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$ROOT/config/cepheus.env"
WORK=${WORK:-$ROOT/work}
OUT=${OUT:-$ROOT/out}
T=$ROOT/tools
DEV=$ROOT/device/cepheus
PY="python3 -I"
export PATH="$ROOT/bin:$PATH"       # optional locally built tools (lpmake, secilc, aapt2)
R8_JAR=${R8_JAR:-$ROOT/bin/r8.jar}
STAGES="deps fetch unpack system vendor boot images preflight super package"

S=$WORK/sys          # ColorOS system tree (root of the system image)
V=$WORK/vendor       # base ROM vendor tree
O=$WORK/odm          # base ROM odm tree (read only, used for policy checks)
H=$WORK/harvest      # small files taken from the base ROM's system side
M=$WORK/mnt

log()  { printf '\n==== %s\n' "$*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }
label() { local ctx=$1; shift; setfattr -h -n security.selinux -v "u:object_r:$ctx:s0" "$@"; }
getlabel() { getfattr -h -n security.selinux --only-values --absolute-names "$1" 2>/dev/null | tr -d '\0'; }
mnt() {  # <image> <dir> [fstype]
  mkdir -p "$2"; mountpoint -q "$2" || mount -o ro,loop ${3:+-t "$3"} "$1" "$2"
}
umount_all() {
  [ -d "$M" ] || return 0
  local d; for d in "$M"/*; do mountpoint -q "$d" && umount "$d" || true; done
}
trap umount_all EXIT

# ---------------------------------------------------------------------------------------------------------
stage_deps() {
  log "deps"
  [ "$(id -u)" = 0 ] || die "run as root (file owners and security.* xattrs must be preserved)"
  local c missing=
  for c in unzip brotli mkfs.erofs dump.erofs lpmake lpdump secilc aapt2 zipalign jarsigner keytool javac java \
           setfattr getfattr nm readelf python3 curl gzip sha256sum md5sum zip; do
    command -v "$c" >/dev/null || missing="$missing $c"
  done
  [ -z "$missing" ] || die "missing tools:$missing (run ./setup.sh, see docs/BUILDING.md)"
  [ -f "$R8_JAR" ] || die "D8 compiler not found at $R8_JAR (run ./setup.sh or set R8_JAR)"
  grep -qw erofs /proc/filesystems || modprobe erofs 2>/dev/null || die "the build host kernel has no EROFS support"
  echo "all tools present"
}

fetch_one() {  # <url> <file> <size>
  if [ -f "$2" ] && [ "$(stat -c%s "$2")" = "$3" ]; then echo "have $(basename "$2")"; return; fi
  if command -v aria2c >/dev/null; then
    aria2c -U "curl/8.5.0" -x 8 -s 8 -k 8M -c --max-tries=10 --retry-wait=5 --summary-interval=0 \
      --console-log-level=warn -d "$(dirname "$2")" -o "$(basename "$2")" "$1"
  else
    curl -fL --retry 5 --retry-delay 5 -C - -o "$2" "$1"
  fi
  [ "$(stat -c%s "$2")" = "$3" ] || die "$(basename "$2"): unexpected size"
}

stage_fetch() {
  log "fetch"
  mkdir -p "$WORK/dl"
  fetch_one "$BASE_URL" "$WORK/dl/base.zip" "$BASE_SIZE"
  echo "$BASE_SHA256  $WORK/dl/base.zip" | sha256sum -c --quiet || die "base ROM checksum mismatch"
  # The donor zip is large; once its system.img is extracted the zip is not needed again.
  if [ -f "$WORK/donor/system.img" ] && [ ! -f "$WORK/dl/donor.zip" ]; then echo "have donor system.img"; return; fi
  fetch_one "$DONOR_URL" "$WORK/dl/donor.zip" "$DONOR_SIZE"
  echo "$DONOR_MD5  $WORK/dl/donor.zip" | md5sum -c --quiet || die "donor checksum mismatch"
  echo "inputs verified"
}

stage_unpack() {
  log "unpack"
  local B=$WORK/base p
  umount_all
  mkdir -p "$B" "$WORK/donor" "$H"
  unzip -o -q "$WORK/dl/base.zip" boot.img dtbo.img vbmeta.img dynamic_partitions_op_list \
    system.transfer.list vendor.transfer.list odm.transfer.list product.transfer.list -d "$B"
  for p in system vendor odm product; do
    [ -f "$B/$p.img" ] || unzip -p "$WORK/dl/base.zip" "$p.new.dat.br" | brotli -dc | $PY "$T/sdat2img.py" "$B/$p.transfer.list" - "$B/$p.img"
  done
  [ -f "$WORK/donor/system.img" ] || unzip -o -q "$WORK/dl/donor.zip" system.img -d "$WORK/donor"

  mnt "$WORK/donor/system.img" "$M/donor"
  mnt "$B/system.img" "$M/bsys"
  mnt "$B/product.img" "$M/bprod"
  mnt "$B/vendor.img" "$M/bvend" erofs
  mnt "$B/odm.img" "$M/bodm" erofs

  # Small pieces of the base ROM's system side that the port needs.
  grep '^ro.bpf.kver_override=' "$M/bprod/etc/build.prop" | cut -d= -f2 > "$H/kver"
  [ -s "$H/kver" ] || die "base ROM does not set ro.bpf.kver_override"
  cp "$M/bsys/system/etc/llndk.libraries.txt" "$H/llndk.libraries.txt"
  if [ -f "$M/bsys/system/apex/com.android.tethering.capex" ]; then
    unzip -p "$M/bsys/system/apex/com.android.tethering.capex" original_apex > "$H/tether-base.apex"
  else
    cp "$M/bsys/system/apex/com.android.tethering.apex" "$H/tether-base.apex"
  fi
  unzip -p "$H/tether-base.apex" apex_payload.img > "$H/tether-base.img"; rm "$H/tether-base.apex"

  # Writable trees. cp -a keeps owners, modes, symlinks and the security.* xattrs.
  rm -rf "$S" "$V" "$O"
  cp -a "$M/donor" "$S"
  cp -a "$M/bvend" "$V"
  cp -a "$M/bodm" "$O"
  [ "$(getlabel "$S/system/bin/init")" = "u:object_r:init_exec:s0" ] || die "SELinux labels were not copied (xattr support?)"
  [ "$(getlabel "$V/etc/fstab.qcom")" = "u:object_r:vendor_configs_file:s0" ] || die "vendor labels were not copied"
  cp "$V/bin/hw/android.hardware.graphics.composer@2.4-service" "$H/composer.orig"
  umount_all
  if [ "${KEEP_IMAGES:-0}" != 1 ]; then
    rm -f "$WORK/donor/system.img" "$B/system.img" "$B/product.img" "$B/vendor.img" "$WORK/dl/donor.zip"
  fi
  echo "trees ready: $(du -sh "$S" | cut -f1) system, $(du -sh "$V" | cut -f1) vendor; kver override $(cat "$H/kver")"
}

# ---------------------------------------------------------------------------------------------------------
tether_override() {
  # ColorOS's com.android.tethering APEX refuses a 4.14 kernel (netbpfload: "Android V requires kernel 4.19",
  # and the same gate in the connectivity JNI libraries) and does not know ro.bpf.kver_override. The base ROM
  # builds the same module with that override. Replacing the whole APEX breaks system_server (its Java needs
  # ColorOS framework classes), so ColorOS's APEX stays and exactly the override-aware native files plus the
  # matching eBPF programs are bind-mounted over it, right before the loader runs.
  local D=$S/system/etc/port-tether rc=$S/system/etc/init/00-port-netbpfload.rc rel lab mode gid types= f
  local files="bin/netbpfload lib/libframework-connectivity-jni.so lib64/libframework-connectivity-jni.so
    lib64/libandroid_net_connectivity_com_android_net_module_util_jni.so lib64/libnetd_resolv.so
    lib64/libnetd_updatable.so lib64/libnetworkstats.so lib64/libservice-connectivity.so"
  [ ! -e "$D" ] && [ ! -e "$rc" ] || die "tether override already applied"
  grep -q '^on load-bpf-programs' "$S/system/etc/init/netbpfload.rc" || die "donor netbpfload.rc changed"
  unzip -p "$S/system/apex/com.android.tethering.apex" apex_payload.img > "$WORK/tether-cos.img"
  mnt "$WORK/tether-cos.img" "$M/acos"; mnt "$H/tether-base.img" "$M/abase"
  install -d -m 0755 -o root -g root "$D"
  # 00-: parsed before netbpfload.rc, so this action runs first in the load-bpf-programs trigger.
  printf '%s\n' "# cepheus port: the base ROM's ro.bpf.kver_override-aware network-stack binaries and eBPF programs" \
    "# over ColorOS's tethering APEX (generated by port.sh)." "on load-bpf-programs" > "$rc"
  for rel in $files; do
    case $rel in lib/*) f="$M/acos/lib $S/system/lib";; *) f="$M/acos/lib64 $S/system/lib64";; esac
    $PY "$T/elf_compat.py" "$M/acos/$rel" "$M/abase/$rel" $f
    grep -q 'ro.bpf.kver_override' "$M/abase/$rel" || die "base $rel does not read ro.bpf.kver_override"
    lab=$(getlabel "$M/acos/$rel"); mode=$(stat -c %a "$M/acos/$rel"); gid=$(stat -c %g "$M/acos/$rel")
    install -D -m "$mode" -o root -g "$gid" "$M/abase/$rel" "$D/$rel"
    setfattr -n security.selinux -v "$lab" "$D/$rel"
    echo "    mount none /system/etc/port-tether/$rel /apex/com.android.tethering/$rel bind" >> "$rc"
    lab=${lab#u:object_r:}; types="$types ${lab%:s0}"
  done
  # eBPF programs: same file set as the donor's, owners/modes/labels taken from the donor's copies.
  [ "$(cd "$M/acos/etc/bpf" && find . | sort)" = "$(cd "$M/abase/etc/bpf" && find . | sort)" ] || die "eBPF program sets differ"
  cp -a "$M/acos/etc/bpf" "$D/bpf"
  (cd "$M/abase/etc/bpf" && find . -type f) | while read -r f; do cat "$M/abase/etc/bpf/$f" > "$D/bpf/$f"; done
  echo "    mount none /system/etc/port-tether/bpf /apex/com.android.tethering/etc/bpf bind" >> "$rc"
  for f in "$D" "$D/bin" "$D/lib" "$D/lib64"; do label system_file "$f"; done
  chmod 0644 "$rc"; label system_file "$rc"
  # init must be allowed to mount on those files/dirs (the port runs permissive, but keep the policy honest).
  local pl=$S/system/etc/selinux/plat_sepolicy.cil t
  for t in $(echo $types | tr ' ' '\n' | sort -u); do
    grep -q "^(allow init $t (file (mounton)))" "$pl" || printf '(allow init %s (file (mounton)))\n' "$t" >> "$pl"
  done
  grep -q '^(allow init system_file (dir (mounton)))' "$pl" || printf '(allow init system_file (dir (mounton)))\n' >> "$pl"
  umount "$M/acos" "$M/abase"; rm -f "$WORK/tether-cos.img"
  echo "tethering override: $(grep -c 'mount none' "$rc") bind mounts"
}

port_services() {
  local f
  # Stand-ins for two Oplus vendor services (see device/common/stubs). They run in the su domain, which this
  # debug-style donor image defines; a hardened build would need its own domain.
  grep -q '^(type su)' "$S/system/etc/selinux/plat_sepolicy.cil" || die "donor policy has no su domain"
  bash "$T/build_stubs.sh" "$ROOT/device/common/stubs" "$WORK/oplus-port-stubs.dex" "$R8_JAR"
  install -m 0644 -o root -g root "$WORK/oplus-port-stubs.dex" "$S/system/framework/oplus-port-stubs.dex"
  install -m 0644 -o root -g root "$ROOT/device/common/init/00-port-oplus-stubs.rc" "$S/system/etc/init/"
  label system_file "$S/system/framework/oplus-port-stubs.dex" "$S/system/etc/init/00-port-oplus-stubs.rc"
  if [ "$BOOTLOG" = 1 ]; then
    install -m 0644 -o root -g root "$ROOT/device/common/init/00-port-bootlog.rc" "$S/system/etc/init/"
    install -m 0755 -o root -g 2000 "$ROOT/device/common/init/port-bootlog.sh" "$S/system/bin/port-bootlog.sh"
    label system_file "$S/system/etc/init/00-port-bootlog.rc" "$S/system/bin/port-bootlog.sh"
  fi
  for f in "$S"/system/etc/init/00-port-*.rc; do
    ! grep -q $'\r' "$f" || die "$f has CRLF line endings"
  done
}

stage_system() {
  log "system"
  [ -f "$S/system/build.prop" ] || die "no system tree (run the unpack stage)"
  grep -q '^ro.port.device=' "$S/system/build.prop" && die "system tree already patched (re-run unpack)"
  $PY "$T/selinux_port.py" fix-system "$S" "$V" "$O"
  $PY "$DEV/adapt_system.py" "$S" "$(cat "$H/kver")" "$H/llndk.libraries.txt"
  tether_override
  port_services

  # Display overlays: replace the donor's two OnePlus 15 panel overlays with Mi 9 builds (same file names).
  local ov=$WORK/overlays f
  rm -rf "$ov"; bash "$T/build_overlays.sh" "$DEV/overlay" "$S/system/framework/framework-res.apk" "$ov" 2>&1 | grep -v JAVA_TOOL_OPTIONS
  for f in android_framework_res_overlay.display.product.24831.apk oplus_framework_res_overlay.display.product.24831.apk; do
    [ -f "$S/my_product/overlay/$f" ] || die "donor overlay $f missing"
    cat "$ov/$f" > "$S/my_product/overlay/$f"            # in place: keeps owner, mode and label
  done

  # Battery: ColorOS reads <partition>/etc/power_profile/power_profile.xml; the GSI's framework default is a
  # placeholder (2 mAh). Take the Mi 9 profile from the base ROM's device overlay.
  local pp=$S/my_product/etc/power_profile
  [ ! -e "$pp" ] || die "donor my_product already has a power profile"
  install -d -m 0755 -o root -g root "$pp"
  aapt2 dump xmltree --file res/xml/power_profile.xml "$V/overlay/FrameworkResOverlayDevice.apk" | $PY "$T/axml2xml.py" > "$pp/power_profile.xml"
  grep -q '<item name="battery.capacity">3300</item>' "$pp/power_profile.xml" || die "unexpected power profile"
  chmod 0644 "$pp/power_profile.xml"; label system_file "$pp" "$pp/power_profile.xml"
  echo "system tree patched"
}

stage_vendor() {
  log "vendor"
  [ -f "$V/build.prop" ] || die "no vendor tree (run the unpack stage)"
  grep -q ' erofs ' <(grep ' /system ' "$V/etc/fstab.qcom") && die "vendor tree already patched (re-run unpack)"
  $PY "$T/patch_fstab.py" "$V/etc/fstab.qcom" "$FBE_MODE"
  $PY "$T/selinux_port.py" fix-vendor "$S" "$V" "$O"
  $PY "$T/patch_composer.py" "$V/bin/hw/android.hardware.graphics.composer@2.4-service"
  if [ "$SOUNDTRIGGER" = remove ]; then
    # The SoundTrigger HIDL adapter lives inside the audio HAL process. On the OnePlus 7 Pro port (same audio
    # HAL family) ColorOS made that process crash-loop in adev_open through the sound-trigger callback and took
    # SurfaceFlinger and system_server down with it. Without the adapter and its manifest entry the sound
    # trigger device is never opened; voice wake-up is lost, everything else in audio is unaffected.
    rm -f "$V"/lib/hw/android.hardware.soundtrigger@*-impl.so "$V"/lib64/hw/android.hardware.soundtrigger@*-impl.so
    $PY - "$V/etc/vintf/manifest.xml" <<'PYEOF'
import re, sys
p = sys.argv[1]; s = open(p).read()
new, n = re.subn(r'[ \t]*<hal format="hidl">\s*<name>android\.hardware\.soundtrigger</name>.*?</hal>[ \t]*\n', '', s, flags=re.S)
assert n == 1, f"expected one soundtrigger HAL entry, found {n}"
with open(p, "r+") as fh: fh.write(new); fh.truncate()
print("vendor manifest: SoundTrigger HAL entry removed")
PYEOF
  fi
  echo "vendor tree patched"
}

stage_boot() {
  log "boot"
  local b=$WORK/boot
  rm -rf "$b"; mkdir -p "$b" "$OUT"
  $PY "$T/bootimg.py" unpack "$WORK/base/boot.img" "$b/x" > "$b/old.info"
  gzip -dc "$b/x/ramdisk" > "$b/ramdisk.cpio"
  $PY "$T/cpio_edit.py" get "$b/ramdisk.cpio" system/etc/fstab.qcom "$b/fstab.qcom"
  $PY "$T/patch_fstab.py" "$b/fstab.qcom" "$FBE_MODE"
  # First-stage and second-stage fstab must describe the same partitions.
  cmp "$b/fstab.qcom" "$V/etc/fstab.qcom" || die "ramdisk fstab differs from the vendor fstab (run the vendor stage first)"
  $PY "$T/cpio_edit.py" put "$b/ramdisk.cpio" system/etc/fstab.qcom "$b/fstab.qcom" "$b/ramdisk.new.cpio"
  gzip -9n -c "$b/ramdisk.new.cpio" > "$b/ramdisk.new"
  $PY "$T/bootimg.py" repack "$WORK/base/boot.img" "$b/ramdisk.new" "$OUT/boot.img"
  # Only the ramdisk may differ from the base boot image.
  $PY "$T/bootimg.py" unpack "$OUT/boot.img" "$b/y" > "$b/new.info"
  cmp "$b/y/kernel" "$b/x/kernel" && cmp "$b/y/dtb" "$b/x/dtb" || die "kernel or dtb changed"
  diff <(grep -v '^ramdisk:' "$b/old.info") <(grep -v '^ramdisk:' "$b/new.info") || die "boot header changed"
  echo "boot.img: $(stat -c%s "$OUT/boot.img") bytes; kernel, dtb, cmdline and header identical to the base, fstab patched"
}

erofs_image() {  # <tree> <image> <mount point>
  rm -f "$2"
  mkfs.erofs -z"$EROFS_COMPRESS" -T "$FIXED_TIME" --mount-point="$3" "$2" "$1" > "$2.log" 2>&1 || { tail -5 "$2.log"; die "mkfs.erofs failed"; }
  # The base kernel's EROFS driver is known to mount images with exactly these features (its own vendor image).
  local feat f
  feat=$(dump.erofs -s "$2" | sed -n 's/^Filesystem features: *//p')
  for f in $feat; do
    case $f in sb_csum|mtime|xattr_filter|0padding) ;; *) die "$2 uses EROFS feature '$f' the base kernel was not seen to support";; esac
  done
  echo "$(basename "$2"): $(stat -c%s "$2") bytes, features: $feat"
}

stage_images() {
  log "images"
  mkdir -p "$OUT"
  erofs_image "$S" "$OUT/system.img" /
  erofs_image "$V" "$OUT/vendor.img" /vendor
  cp "$WORK/base/odm.img" "$OUT/odm.img"
  cp "$WORK/base/dtbo.img" "$WORK/base/vbmeta.img" "$OUT/"
  # vbmeta must have verification disabled: neither system nor vendor carries dm-verity metadata.
  [ "$($PY -c "import struct,sys; print(struct.unpack('>I', open(sys.argv[1],'rb').read()[120:124])[0])" "$OUT/vbmeta.img")" = 3 ] \
    || die "base vbmeta does not have verity and verification disabled"
}

stage_preflight() {
  log "preflight"
  WORK=$WORK OUT=$OUT bash "$T/preflight.sh"
}

stage_super() {
  log "super"
  local sz total=0 p args=() dev
  rm -rf "$OUT/super"; mkdir -p "$OUT/super"
  for dev in $SUPER_DEVICES; do args+=(--device "$dev"); done
  args+=(--group "$SUPER_GROUP:$SUPER_GROUP_SIZE")
  for p in system odm vendor; do
    sz=$(stat -c%s "$OUT/$p.img"); [ $((sz % 4096)) = 0 ] || die "$p.img is not block aligned"
    total=$((total + sz))
    args+=(--partition "$p:readonly:$sz:$SUPER_GROUP" --image "$p=$OUT/$p.img")
  done
  [ "$total" -le "$SUPER_GROUP_SIZE" ] || die "images ($total bytes) do not fit the super group ($SUPER_GROUP_SIZE bytes)"
  lpmake --metadata-size "$SUPER_METADATA_SIZE" --super-name "$SUPER_NAME" --metadata-slots "$SUPER_METADATA_SLOTS" \
    "${args[@]}" --sparse --output "$OUT/super"
  ls -l "$OUT/super"
  $PY "$T/verify_super.py" "$OUT/super" "$SUPER_NAME" system="$OUT/system.img" odm="$OUT/odm.img" vendor="$OUT/vendor.img"
  echo "super: $total of $SUPER_GROUP_SIZE bytes used ($(( (SUPER_GROUP_SIZE - total) / 1048576 )) MiB free)"
  # SLIM=1 (CI): the partition images are now inside the verified super images.
  if [ "${SLIM:-0}" = 1 ]; then rm -f "$OUT/system.img" "$OUT/vendor.img"; fi
}

stage_package() {
  log "package"
  local name="ColorOS17-PLK110-cepheus-$(date -u +%Y%m%d)" pkg=$WORK/pkg f
  rm -rf "$pkg"; mkdir -p "$pkg"
  for f in boot.img dtbo.img vbmeta.img; do cp "$OUT/$f" "$pkg/"; done
  # A super member that no logical partition reaches is an empty sparse image: leave that partition alone.
  for f in "$OUT"/super/super_*.img; do
    if [ "$(stat -c%s "$f")" -lt 4096 ]; then echo "$(basename "$f") carries no data: not packaged"; continue; fi
    ln "$f" "$pkg/" 2>/dev/null || cp "$f" "$pkg/"
  done
  [ -f "$pkg/super_system.img" ] && [ -f "$pkg/super_vendor.img" ] || die "super images missing"
  cp "$ROOT/installer/flash_all.sh" "$ROOT/installer/flash_all.bat" "$ROOT/installer/README.txt" "$pkg/"
  chmod +x "$pkg/flash_all.sh"
  (cd "$pkg" && sha256sum *.img > SHA256SUMS)
  rm -f "$OUT/$name.zip"
  (cd "$pkg" && zip -0 -q "$OUT/$name.zip" *)       # images are already compressed
  (cd "$OUT" && sha256sum "$name.zip" > "$name.zip.sha256")
  echo "package: $OUT/$name.zip ($(du -h "$OUT/$name.zip" | cut -f1))"
}

# ---------------------------------------------------------------------------------------------------------
run=${*:-$STAGES}
for st in $run; do
  case " $STAGES " in *" $st "*) ;; *) die "unknown stage '$st' (stages: $STAGES)";; esac
  "stage_$st"
done
log "done: $run"
