#!/usr/bin/env python3
"""Check that the vendor image's native code still links against the ColorOS system image.

Usage: vendor_link_check.py <vendor root> <odm root> <system root> <base LLNDK dir> <ColorOS bionic dir>

Vendor binaries may only use LLNDK libraries from the system image: the names in
system/etc/llndk.libraries.txt, loaded from system/lib{,64} (bionic's libc/libm/libdl come from the runtime
APEX; pass the directory holding its lib/bionic and lib64/bionic). The vendor image was built against the base
ROM's system, so the check is differential:

  1. every library a vendor ELF file needs and the vendor image does not ship must be an LLNDK library that
     ColorOS lists and provides. A library the base ROM does not provide either is reported and ignored
     (it is not a regression of the port, typically DSP-side code);
  2. every symbol a vendor ELF file imports and the base ROM's copy of an LLNDK library exports must also be
     exported by ColorOS's copy.

<base LLNDK dir> holds lib/ and lib64/ with the base ROM's LLNDK libraries (collected by `port.sh unpack`).
A failure here is what shows up on the phone as "CANNOT LINK EXECUTABLE" for a HAL service.
"""
import collections
import os
import subprocess
import sys

BIONIC = ("libc.so", "libm.so", "libdl.so")


def run(args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def elf_class(path):
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head[:4] != b"\x7fELF":
        return None
    return "lib64" if head[4] == 2 else "lib"


def needed(path):
    return [line.split("[")[1].split("]")[0] for line in run(["readelf", "-d", path]).splitlines() if "(NEEDED)" in line]


def symbols(path, kind):
    return {line.split()[-1].split("@")[0] for line in run(["nm", "-D", f"--{kind}-only", path]).splitlines() if line.strip()}


def main():
    vendor, odm, system, base, bionic = sys.argv[1:6]
    elfs = []
    for root in (vendor, odm):
        for d, _, files in os.walk(root):
            for name in files:
                p = os.path.join(d, name)
                if not os.path.islink(p):
                    cls = elf_class(p)
                    if cls:
                        elfs.append((p, cls))
    shipped = {"lib": set(), "lib64": set()}
    for p, cls in elfs:
        if p.endswith(".so"):
            shipped[cls].add(os.path.basename(p))
    llndk = set(open(os.path.join(system, "system/etc/llndk.libraries.txt")).read().split())

    def cos_lib(cls, lib):
        if lib in BIONIC:
            return os.path.join(bionic, cls, "bionic", lib)
        return os.path.join(system, "system", cls, lib)

    exports = {}

    def exported(path):
        if path not in exports:
            exports[path] = symbols(path, "defined") if os.path.isfile(path) else None
        return exports[path]

    missing_lib = collections.defaultdict(set)
    ignored = collections.defaultdict(set)
    missing_sym = collections.defaultdict(lambda: collections.defaultdict(set))
    for p, cls in elfs:
        rel = os.path.relpath(p, os.path.dirname(vendor))
        from_system = [n for n in needed(p) if n not in shipped[cls]]
        if not from_system:
            continue
        undefined = symbols(p, "undefined")
        for lib in from_system:
            base_path = os.path.join(base, cls, lib)
            cos_path = cos_lib(cls, lib)
            if not os.path.isfile(base_path):
                ignored[(cls, lib)].add(rel)             # the base ROM's system does not provide it either
                continue
            if lib not in llndk or exported(cos_path) is None:
                missing_lib[(cls, lib)].add(rel)
                continue
            lost = (undefined & exported(base_path)) - exported(cos_path)
            if lost:
                missing_sym[(cls, lib)][rel] |= lost

    print(f"{len(elfs)} vendor/odm ELF files checked")
    for (cls, lib), users in sorted(ignored.items()):
        print(f"  note: {cls}/{lib} is not provided by the base ROM either ({len(users)} user(s), e.g. {sorted(users)[0]})")
    for (cls, lib), users in sorted(missing_lib.items()):
        print(f"  MISSING {cls}/{lib}: not LLNDK or not in the ColorOS system; {len(users)} user(s), e.g. {sorted(users)[0]}")
    for (cls, lib), users in sorted(missing_sym.items()):
        syms = sorted(set().union(*users.values()))
        print(f"  MISSING SYMBOLS in {cls}/{lib}: {syms[:8]} ({len(syms)} total), used by e.g. {sorted(users)[:3]}")
    if missing_lib or missing_sym:
        sys.exit(1)
    print("every system library and symbol the vendor code needs is provided by ColorOS")


if __name__ == "__main__":
    main()
