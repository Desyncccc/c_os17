#!/usr/bin/env python3
"""Check that one ELF file can stand in for another inside a linker namespace.

Usage: elf_compat.py <original> <replacement> <library search dir>...

The replacement is accepted when
  * it defines exactly the same dynamic symbols as the original (users of the file see no difference), and
  * every symbol it imports that the original did not import is exported by one of its DT_NEEDED libraries
    found in the search dirs (bionic's versioned libc/libm/libdl symbols are taken as present: those
    libraries live in the runtime APEX and are identical for both builds of Android 17).
Exit status 1 with a message otherwise.
"""
import os
import subprocess
import sys

NM = os.environ.get("NM", "nm")
READELF = os.environ.get("READELF", "readelf")
BIONIC = {"libc.so", "libm.so", "libdl.so"}


def syms(path, kind):
    out = subprocess.run([NM, "-D", f"--{kind}-only", path], capture_output=True, text=True).stdout
    return {line.split()[-1] for line in out.splitlines() if line.strip()}


def needed(path):
    out = subprocess.run([READELF, "-d", path], capture_output=True, text=True).stdout
    return [line.split("[")[1].split("]")[0] for line in out.splitlines() if "(NEEDED)" in line]


def main():
    orig, repl, dirs = sys.argv[1], sys.argv[2], sys.argv[3:]
    a, b = syms(orig, "defined"), syms(repl, "defined")
    if a != b:
        sys.exit(f"{repl}: exported symbols differ from {orig}: "
                 f"missing {sorted(a - b)[:5]} extra {sorted(b - a)[:5]}")
    new = syms(repl, "undefined") - syms(orig, "undefined")
    unresolved = {s for s in new if "@LIBC" not in s}
    libs = []
    for lib in needed(repl):
        if lib in BIONIC:
            continue
        for d in dirs:
            if os.path.exists(os.path.join(d, lib)):
                libs.append(os.path.join(d, lib))
                break
    for lib in libs:
        if not unresolved:
            break
        exported = {s.split("@")[0] for s in syms(lib, "defined")}
        unresolved = {s for s in unresolved if s.split("@")[0] not in exported}
    if unresolved:
        sys.exit(f"{repl}: new imports not provided by the donor's libraries: {sorted(unresolved)}")
    print(f"ok  {os.path.basename(repl)}: same exports, {len(new)} new imports resolved")


if __name__ == "__main__":
    main()
