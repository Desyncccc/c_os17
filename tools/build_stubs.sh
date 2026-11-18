#!/usr/bin/env bash
# Compile device/common/stubs into a dex file for app_process.
#
#   build_stubs.sh <stubs dir> <output dex> <r8.jar>
#
# Needs a JDK (javac, java) and the R8/D8 jar (com.android.tools:r8 from Google's Maven repository).
set -euo pipefail
SRC=$1; OUT=$2; R8=$3
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/api" "$tmp/classes" "$tmp/dex"
# The platform classes are only needed to compile against; they are not put into the dex.
javac -nowarn --release 11 -d "$tmp/api" $(find "$SRC/android-stubs" -name '*.java') 2>&1 | grep -v JAVA_TOOL_OPTIONS || true
javac -nowarn --release 11 -cp "$tmp/api" -d "$tmp/classes" $(find "$SRC/src" -name '*.java') 2>&1 | grep -v JAVA_TOOL_OPTIONS || true
[ -f "$tmp/classes/port/OplusStubs.class" ] || { echo "javac failed"; exit 1; }
java -cp "$R8" com.android.tools.r8.D8 --release --min-api 34 --no-desugaring --classpath "$tmp/api" \
  --output "$tmp/dex" $(find "$tmp/classes" -name '*.class') 2>&1 | grep -v JAVA_TOOL_OPTIONS || true
[ -f "$tmp/dex/classes.dex" ] || { echo "d8 failed"; exit 1; }
cp "$tmp/dex/classes.dex" "$OUT"
echo "built $OUT ($(stat -c%s "$OUT") bytes)"
