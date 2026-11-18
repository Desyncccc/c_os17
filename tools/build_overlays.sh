#!/usr/bin/env bash
# Build the Mi 9 replacements for the donor's two display overlays (static RROs in /my_product/overlay).
#
#   build_overlays.sh <overlay source dir> <framework-res.apk of the donor> <output dir>
#
# Needs aapt2, zipalign, keytool and jarsigner (JDK). The APKs are JAR-signed (v1) with a throw-away key, like the
# donor's own overlays are v1-only: packages on system partitions are scanned without signature verification
# and static overlays need no particular signer.
set -euo pipefail
SRC=$1; FWRES=$2; OUT=$3
AAPT2=${AAPT2:-aapt2}
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$OUT"

keytool -genkeypair -keystore "$tmp/ks.p12" -storetype PKCS12 -storepass android -keypass android \
  -alias port -keyalg RSA -keysize 2048 -validity 10950 -dname "CN=cepheus ColorOS port overlays" >/dev/null 2>&1

build() {  # <source dir> <output apk>
  local name; name=$(basename "$1")
  mkdir -p "$tmp/$name"
  "$AAPT2" compile --dir "$1/res" -o "$tmp/$name/res.zip"
  "$AAPT2" link -o "$tmp/$name/unsigned.apk" -I "$FWRES" --manifest "$1/AndroidManifest.xml" \
    --min-sdk-version 37 --target-sdk-version 37 --no-resource-removal "$tmp/$name/res.zip"
  jarsigner -keystore "$tmp/ks.p12" -storetype PKCS12 -storepass android -keypass android \
    -sigalg SHA256withRSA -digestalg SHA-256 -signedjar "$tmp/$name/signed.apk" "$tmp/$name/unsigned.apk" port >/dev/null
  # resources.arsc must be stored uncompressed on a 4-byte boundary (apps targeting API 30+). A v1 signature
  # covers entry contents only, so aligning after signing keeps it valid.
  zipalign -f 4 "$tmp/$name/signed.apk" "$2"
  zipalign -c 4 "$2"
  jarsigner -verify "$2" >/dev/null
  "$AAPT2" dump resources "$2" > "$2.resources.txt"
  echo "built $2 ($(stat -c%s "$2") bytes)"
}

# android (framework-res) overlay: plain value resources.
build "$SRC/android_display" "$OUT/android_framework_res_overlay.display.product.24831.apk"

# oplus overlay: transparent 260x260 masks, same names and density bucket as the donor's drawables.
gen="$tmp/oplus_src"; mkdir -p "$gen/res/drawable-xxhdpi"
cp "$SRC/oplus_display/AndroidManifest.xml" "$gen/"
python3 -I - "$gen/res/drawable-xxhdpi" <<'PY'
import struct, sys, zlib
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
w = h = 260
raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * w for _ in range(h))          # RGBA, every pixel transparent
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) \
      + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
for n in ("oplus_rc_bottom_left", "oplus_rc_bottom_right", "oplus_rc_cutout", "oplus_rc_top_left", "oplus_rc_top_right"):
    open(f"{sys.argv[1]}/{n}.png", "wb").write(png)
PY
build "$gen" "$OUT/oplus_framework_res_overlay.display.product.24831.apk"
