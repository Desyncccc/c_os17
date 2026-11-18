#!/usr/bin/env bash
# Prepare an Ubuntu 24.04 (or Debian 12+) host for port.sh. Run once as root:  sudo ./setup.sh
#
# Installs distribution packages and puts the tools the distribution does not provide (or provides too old)
# into ./bin, which port.sh adds to PATH:
#   lpmake, lpdump   Android logical-partition tools, built from the android-tools source release
#   secilc           SELinux CIL compiler 3.11 (the distribution's 3.5 rejects Android 17 policy syntax)
#   aapt2            Android resource compiler, from Google's Maven repository
#   r8.jar           D8 dex compiler, from Google's Maven repository
#   apktool.jar      smali assembler/disassembler, from the Apktool GitHub releases
# Every download is pinned by SHA-256.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BIN=$ROOT/bin
BUILD=${BUILD:-$ROOT/work/host-build}
mkdir -p "$BIN" "$BUILD"

ANDROID_TOOLS_VER=37.0.0p1
ANDROID_TOOLS_SHA256=2c6de1abe16b211c7d9f59276217e14fd9e0e18b20e331d3c84358f5dffd576e
SELINUX_TAG=3.11
SELINUX_COMMIT=2233a23a4d4f1bf29054037babec13f30d038e65
AAPT2_VER=8.13.0-13719691
AAPT2_SHA256=c1aebd96a144313da65de675cc1f59041b41a52e844228d311bb580ed830b0d9
R8_VER=8.13.25
R8_SHA256=0d4f2f68c4fcbc9434eff122f41feb6fb1d98efd2faa530cec1abacab1cea03d
APKTOOL_VER=3.0.3
APKTOOL_SHA256=dbf930b076c6b9be08d57c449cacefc3bdd6b71ebd59b3066fc0e1f5b14f9423

get() {  # <url> <file> <sha256>
  [ -f "$2" ] && echo "$3  $2" | sha256sum -c --quiet 2>/dev/null && return
  curl -fsSL --retry 5 -o "$2" "$1"
  echo "$3  $2" | sha256sum -c --quiet
}

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  aria2 attr binutils brotli build-essential ca-certificates cmake curl erofs-utils flex bison git \
  android-sdk-libsparse-utils golang-go libbrotli-dev libgtest-dev liblz4-dev libpcre2-dev libprotobuf-dev \
  libusb-1.0-0-dev libzstd-dev ninja-build openjdk-21-jdk-headless pkg-config protobuf-compiler python3 \
  unzip xz-utils zip zipalign

if [ ! -x "$BIN/lpmake" ] || [ ! -x "$BIN/lpdump" ]; then
  get "https://github.com/nmeum/android-tools/releases/download/$ANDROID_TOOLS_VER/android-tools-$ANDROID_TOOLS_VER.tar.xz" \
      "$BUILD/android-tools.tar.xz" "$ANDROID_TOOLS_SHA256"
  rm -rf "$BUILD/android-tools"; mkdir -p "$BUILD/android-tools"
  tar -xJf "$BUILD/android-tools.tar.xz" -C "$BUILD/android-tools" --strip-components=1
  cmake -S "$BUILD/android-tools" -B "$BUILD/android-tools/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DANDROID_TOOLS_USE_BUNDLED_FMT=ON >/dev/null
  ninja -C "$BUILD/android-tools/build" lpmake lpdump
  cp "$BUILD/android-tools/build/vendor/lpmake" "$BUILD/android-tools/build/vendor/lpdump" "$BIN/"
fi

if [ ! -x "$BIN/secilc" ]; then
  rm -rf "$BUILD/selinux"
  git clone -q --depth 1 --branch "$SELINUX_TAG" https://github.com/SELinuxProject/selinux.git "$BUILD/selinux"
  [ "$(git -C "$BUILD/selinux" rev-parse HEAD)" = "$SELINUX_COMMIT" ] || { echo "unexpected selinux commit"; exit 1; }
  make -s -C "$BUILD/selinux/libsepol/src" -j"$(nproc)" libsepol.a
  gcc -O2 -o "$BIN/secilc" "$BUILD/selinux/secilc/secilc.c" -I "$BUILD/selinux/libsepol/include" \
      -I "$BUILD/selinux/libsepol/cil/include" "$BUILD/selinux/libsepol/src/libsepol.a"
fi

if [ ! -x "$BIN/aapt2" ]; then
  get "https://dl.google.com/dl/android/maven2/com/android/tools/build/aapt2/$AAPT2_VER/aapt2-$AAPT2_VER-linux.jar" \
      "$BUILD/aapt2.jar" "$AAPT2_SHA256"
  unzip -o -q "$BUILD/aapt2.jar" aapt2 -d "$BIN"; chmod +x "$BIN/aapt2"
fi
get "https://dl.google.com/dl/android/maven2/com/android/tools/r8/$R8_VER/r8-$R8_VER.jar" "$BIN/r8.jar" "$R8_SHA256"
get "https://github.com/iBotPeaches/Apktool/releases/download/v$APKTOOL_VER/apktool_$APKTOOL_VER.jar" "$BIN/apktool.jar" "$APKTOOL_SHA256"

echo "host tools ready in $BIN:"; ls "$BIN"
