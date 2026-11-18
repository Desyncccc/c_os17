#!/usr/bin/env python3
"""Minimal Android boot image tool (header versions 0-2), enough to swap the ramdisk of an existing image.

  bootimg.py info   <boot.img>
  bootimg.py unpack <boot.img> <out dir>            writes kernel, ramdisk, second, recovery_dtbo, dtb
  bootimg.py repack <boot.img> <new ramdisk> <out>  same image with another ramdisk

`repack` copies the original header byte for byte and changes only what depends on the ramdisk: its size,
the recovery_dtbo offset and the image id (SHA-1 over the sections, as mkbootimg computes it). Before writing,
the id algorithm is checked against the original image's own id, so a wrong assumption cannot go unnoticed.
Anything after the last section of the original (padding, AVB footer) is not copied.
"""
import hashlib
import struct
import sys
from pathlib import Path

MAGIC = b"ANDROID!"


class BootImage:
    def __init__(self, data):
        assert data[:8] == MAGIC, "not an Android boot image"
        (self.kernel_size, self.kernel_addr, self.ramdisk_size, self.ramdisk_addr, self.second_size,
         self.second_addr, self.tags_addr, self.page_size, self.header_version, self.os_version) = \
            struct.unpack_from("<10I", data, 8)
        assert self.header_version <= 2, f"boot header version {self.header_version} is not supported"
        self.name = data[48:64].split(b"\0")[0].decode()
        self.cmdline = (data[64:576].split(b"\0")[0] + data[608:1632].split(b"\0")[0]).decode()
        self.id = data[576:608]
        self.recovery_dtbo_size = self.dtb_size = 0
        self.header_size = 1632
        if self.header_version >= 1:
            self.recovery_dtbo_size, self.recovery_dtbo_offset, self.header_size = struct.unpack_from("<IQI", data, 1632)
        if self.header_version >= 2:
            self.dtb_size, self.dtb_addr = struct.unpack_from("<IQ", data, 1648)
        self.header = data[:self.header_size]
        pos = self.page_size
        self.sections = {}
        for name in ("kernel", "ramdisk", "second", "recovery_dtbo", "dtb"):
            size = getattr(self, name + "_size")
            self.sections[name] = data[pos:pos + size]
            assert len(self.sections[name]) == size, f"{name} is truncated"
            pos += self._pages(size) * self.page_size

    def _pages(self, size):
        return (size + self.page_size - 1) // self.page_size

    def compute_id(self):
        sha = hashlib.sha1()
        names = ["kernel", "ramdisk", "second"]
        if self.header_version > 0:
            names.append("recovery_dtbo")
        if self.header_version > 1:
            names.append("dtb")
        for name in names:
            sha.update(self.sections[name])
            sha.update(struct.pack("<I", len(self.sections[name])))
        return sha.digest().ljust(32, b"\0")

    def build(self):
        hdr = bytearray(self.header)
        struct.pack_into("<I", hdr, 16, len(self.sections["ramdisk"]))
        if self.header_version >= 1 and self.recovery_dtbo_size:
            off = self.page_size * (1 + sum(self._pages(len(self.sections[n])) for n in ("kernel", "ramdisk", "second")))
            struct.pack_into("<Q", hdr, 1636, off)
        hdr[576:608] = self.compute_id()
        out = bytearray(hdr.ljust(self.page_size, b"\0"))
        for name in ("kernel", "ramdisk", "second", "recovery_dtbo", "dtb"):
            sec = self.sections[name]
            out += sec + b"\0" * (self._pages(len(sec)) * self.page_size - len(sec))
        return bytes(out)

    def info(self):
        a, b, c = self.os_version >> 25, (self.os_version >> 18) & 0x7F, (self.os_version >> 11) & 0x7F
        y, m = 2000 + ((self.os_version >> 4) & 0x7F), self.os_version & 0xF
        lines = [f"header version: {self.header_version}", f"page size: {self.page_size}",
                 f"os version: {a}.{b}.{c}", f"os patch level: {y}-{m:02d}", f"name: {self.name}",
                 f"cmdline: {self.cmdline}",
                 f"kernel: {self.kernel_size} bytes @ 0x{self.kernel_addr:08x}",
                 f"ramdisk: {len(self.sections['ramdisk'])} bytes @ 0x{self.ramdisk_addr:08x}",
                 f"second: {self.second_size} bytes", f"tags @ 0x{self.tags_addr:08x}",
                 f"recovery_dtbo: {self.recovery_dtbo_size} bytes"]
        if self.header_version >= 2:
            lines.append(f"dtb: {self.dtb_size} bytes @ 0x{self.dtb_addr:08x}")
        return "\n".join(lines)


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    img = BootImage(Path(path).read_bytes())
    if cmd == "info":
        print(img.info())
    elif cmd == "unpack":
        out = Path(sys.argv[3])
        out.mkdir(parents=True, exist_ok=True)
        for name, data in img.sections.items():
            if data:
                (out / name).write_bytes(data)
        print(img.info())
    elif cmd == "repack":
        assert img.compute_id() == img.id, "id algorithm does not reproduce the original image's id"
        img.sections["ramdisk"] = Path(sys.argv[3]).read_bytes()
        Path(sys.argv[4]).write_bytes(img.build())
        print(f"{sys.argv[4]}: ramdisk {img.ramdisk_size} -> {len(img.sections['ramdisk'])} bytes")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
