#!/usr/bin/env python3
"""Stop a binary from waiting for AIDL HALs that this device does not declare.

Usage: patch_sf_waits.py patch  <binary> <vintf manifest file or directory>...
       patch_sf_waits.py verify <original> <patched> <vintf manifest file or directory>...

ColorOS's SurfaceFlinger fetches some Oplus and Pixelworks display HALs with AServiceManager_waitForService,
which blocks until the service is registered. On a vendor image without those HALs that is forever, and the
caller is SurfaceFlinger's main thread: the screen freezes (seen on the Xiaomi Pad 6 port when Game Filter
was switched on). Each such call is followed by the typed `I<Name>::fromBinder` conversion, which names the
interface. For every interface whose package no given VINTF manifest declares as an AIDL HAL the call is
redirected to AServiceManager_checkService: same signature, returns null at once, and null is what the code
after the call already tests for. Waits for declared HALs are left alone, since those services do appear.

Only the 4-byte `bl` instructions change. `patch` writes just those bytes in place, so owner, mode and
extended attributes stay. `verify` applies the same edit to the original in memory and requires the patched
file to be exactly that. The tool stops if a wait is not followed by a recognisable conversion.
"""
import re
import struct
import sys
from pathlib import Path

WAIT, CHECK = "AServiceManager_waitForService", "AServiceManager_checkService"
FROM_BINDER = "10fromBinderERKN3ndk10SpAIBinderE"
BTI_C = 0xD503245F


def sx(value, bits):
    return (value ^ (1 << (bits - 1))) - (1 << (bits - 1))


class Elf:
    def __init__(self, data):
        self.d = data
        assert data[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", data, 18)[0] == 183, "not an AArch64 ELF64 file"
        shoff, = struct.unpack_from("<Q", data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
        raw = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize) for i in range(shnum)]
        names = raw[shstrndx]
        self.sec = {}
        for name, _type, _flags, addr, off, size, link, _info, _align, _entsize in raw:
            end = data.index(b"\0", names[4] + name)
            self.sec[data[names[4] + name:end].decode()] = (addr, off, size, link)
        self.raw = raw

    def word(self, section, addr):
        base, off, size, _ = self.sec[section]
        assert base <= addr < base + size
        return struct.unpack_from("<I", self.d, off + addr - base)[0]

    def plt(self):
        """Address of each PLT stub by symbol name (the address a `bl` uses, BTI landing pad included)."""
        _, symoff, _, strndx = self.sec[".dynsym"]
        stroff = self.raw[strndx][4]
        slots = {}
        _, off, size, _ = self.sec[".rela.plt"]
        for o in range(off, off + size, 24):
            r_offset, r_info, _addend = struct.unpack_from("<QQq", self.d, o)
            st_name, = struct.unpack_from("<I", self.d, symoff + 24 * (r_info >> 32))
            end = self.d.index(b"\0", stroff + st_name)
            slots[r_offset] = self.d[stroff + st_name:end].decode()
        base, _, size, _ = self.sec[".plt"]
        stubs = {}
        for pc in range(base, base + size - 12, 4):
            adrp, ldr, add, br = (self.word(".plt", pc + n) for n in (0, 4, 8, 12))
            if adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211 or br != 0xD61F0220:
                continue                                   # adrp x16 / ldr x17, [x16, #n] / add x16 / br x17
            page = (pc & ~0xFFF) + (sx(((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3, 21) << 12)
            slot = page + ((ldr >> 10) & 0xFFF) * 8
            if slot in slots:
                entry = pc - 4 if pc > base and self.word(".plt", pc - 4) == BTI_C else pc
                assert slots[slot] not in stubs, f"two PLT stubs for {slots[slot]}"
                stubs[slots[slot]] = entry
        return stubs


def interface(symbol):
    """'_ZN4aidl6vendor5oplus...9IMixLut3D10fromBinderE...' -> ('vendor.oplus...', 'IMixLut3D')."""
    assert symbol.startswith("_ZN4aidl") and symbol.endswith(FROM_BINDER), symbol
    rest, parts = symbol[len("_ZN4aidl"):-len(FROM_BINDER)], []
    while rest:
        n = re.match(r"\d+", rest).group()
        parts.append(rest[len(n):len(n) + int(n)])
        rest = rest[len(n) + int(n):]
    return ".".join(parts[:-1]), parts[-1]


def declared_packages(paths):
    """Packages of the AIDL HALs declared in the VINTF manifests under `paths`, and the number of HAL entries read."""
    names, hals = set(), 0
    for p in map(Path, paths):
        for f in ([p] if p.is_file() else sorted(p.rglob("*.xml")) if p.is_dir() else []):
            text = f.read_text(errors="replace")
            if not re.search(r"<manifest\b", text):      # manifests declare; compatibility matrices only list
                continue
            for attrs, body in re.findall(r"<hal\b([^>]*)>(.*?)</hal>", text, re.S):
                hals += 1
                name = re.search(r"<name>\s*([\w.]+)\s*</name>", body)
                if name and re.search(r'format\s*=\s*"aidl"', attrs):
                    names.add(name.group(1))
    return names, hals


def edit(data, manifests):
    """Return the edited copy of `data` and one report line per wait site."""
    elf, out = Elf(data), bytearray(data)
    stubs = elf.plt()
    assert WAIT in stubs and CHECK in stubs, "the binary must import both lookups"
    by_addr = {addr: name for name, addr in stubs.items()}
    declared, hals = declared_packages(manifests)
    assert hals >= 20 and declared, "the manifests given do not look like a device's"
    base, off, size, _ = elf.sec[".text"]

    def bl_target(pc):
        w = elf.word(".text", pc)
        return pc + sx(w & 0x3FFFFFF, 26) * 4 if w & 0xFC000000 == 0x94000000 else None

    report = []
    for pc in range(base, base + size, 4):
        if bl_target(pc) != stubs[WAIT]:
            continue
        conv = None
        for nxt in range(pc + 4, min(pc + 40, base + size), 4):   # straight-line code up to the conversion
            w = elf.word(".text", nxt)
            if bl_target(nxt) is not None:
                conv = by_addr.get(bl_target(nxt))
                break
            if (w & 0x7C000000 == 0x14000000 or w & 0xFF000010 == 0x54000000 or
                    w & 0x7E000000 in (0x34000000, 0x36000000) or w & 0xFE000000 == 0xD6000000):
                break                                              # b, b.cond, cbz/cbnz, tbz/tbnz, br/blr/ret
        assert conv and conv.endswith(FROM_BINDER), f"wait at {pc:#x}: no typed fromBinder call follows"
        package, name = interface(conv)
        if package in declared:
            report.append(f"{pc:#x} {package}.{name}: declared on this device, wait kept")
            continue
        delta = stubs[CHECK] - pc
        assert delta % 4 == 0 and -(1 << 27) <= delta < (1 << 27)
        struct.pack_into("<I", out, off + pc - base, 0x94000000 | (delta >> 2) & 0x3FFFFFF)
        report.append(f"{pc:#x} {package}.{name}: not declared, waitForService -> checkService")
    assert report, "no waitForService call found"
    return bytes(out), report


def main():
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "patch":
        data = Path(a[1]).read_bytes()
        out, report = edit(data, a[2:])
        with open(a[1], "r+b") as f:
            for i in range(0, len(data), 4):
                if data[i:i + 4] != out[i:i + 4]:
                    f.seek(i)
                    f.write(out[i:i + 4])
        print("\n".join(f"{Path(a[1]).name}: {line}" for line in report))
    elif len(a) >= 4 and a[0] == "verify":
        expect, report = edit(Path(a[1]).read_bytes(), a[3:])
        if Path(a[2]).read_bytes() != expect:
            sys.exit("the patched file is not the original with exactly these calls redirected")
        changed = sum("->" in line for line in report)
        if not changed:
            sys.exit("nothing was redirected")
        print(f"{changed} of {len(report)} waits redirected: " + "; ".join(line.split(":")[0].split(" ", 1)[1] for line in report if "->" in line))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
