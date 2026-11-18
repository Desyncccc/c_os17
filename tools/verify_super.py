#!/usr/bin/env python3
"""Independent check of the split (retrofit) super images produced by lpmake.

Usage: verify_super.py <dir with super_<device>.img> <metadata device name> <partition>=<image>...

Reads the Android sparse images directly, parses the logical-partition metadata from the first device with
its own parser (not liblp), follows every extent of every partition across the block devices and compares
the SHA-256 of that data with the image it was built from. This is exactly what first-stage init will map
with dm-linear, so a pass means each logical partition on the phone will be byte-identical to its image.
"""
import hashlib
import struct
import sys
from pathlib import Path

SECTOR = 512
SPARSE_MAGIC = 0xED26FF3A
LP_GEOMETRY_MAGIC = 0x616C4467
LP_HEADER_MAGIC = 0x414C5030


class Sparse:
    def __init__(self, path):
        self.f = open(path, "rb")
        magic, major, minor, fh, ch, self.blk, self.total_blks, chunks, _ = struct.unpack("<IHHHHIIII", self.f.read(28))
        assert magic == SPARSE_MAGIC and major == 1, f"{path}: not an Android sparse image"
        self.f.seek(fh)
        self.map, block = [], 0          # (first block, block count, kind, file offset / fill bytes)
        for _ in range(chunks):
            ctype, _, nblk, total = struct.unpack("<HHII", self.f.read(ch))
            body = self.f.tell()
            if ctype == 0xCAC1:
                self.map.append((block, nblk, "raw", body))
            elif ctype == 0xCAC2:
                self.map.append((block, nblk, "fill", self.f.read(4)))
            elif ctype == 0xCAC3:
                self.map.append((block, nblk, "skip", None))
            if ctype != 0xCAC4:
                block += nblk
            self.f.seek(body + total - ch)
        assert block == self.total_blks, f"{path}: chunk table does not cover the image"
        self.size = self.total_blks * self.blk

    def read(self, offset, length):
        out = bytearray()
        while length:
            blk = offset // self.blk
            for first, n, kind, data in self.map:
                if first <= blk < first + n:
                    break
            else:
                raise SystemExit("read outside the sparse image")
            avail = (first + n) * self.blk - offset
            take = min(avail, length)
            if kind == "raw":
                self.f.seek(data + offset - first * self.blk)
                out += self.f.read(take)
            elif kind == "fill":
                rep = (data * (take // 4 + 2))[offset % 4: offset % 4 + take]
                out += rep
            else:
                out += b"\0" * take
            offset += take
            length -= take
        return bytes(out)


def cstr(b):
    return b.split(b"\0", 1)[0].decode()


def main():
    d, meta_dev = Path(sys.argv[1]), sys.argv[2]
    images = dict(a.split("=", 1) for a in sys.argv[3:])
    devs = {p.name[len("super_"):-len(".img")]: Sparse(p) for p in sorted(d.glob("super_*.img"))}
    first = devs[meta_dev]

    geo = first.read(4096, 4096)
    magic, struct_size = struct.unpack_from("<II", geo)
    assert magic == LP_GEOMETRY_MAGIC, "no LP geometry on the metadata device"
    max_size, slots, lbs = struct.unpack_from("<III", geo, 40)
    body = geo[:struct_size]
    assert hashlib.sha256(body[:8] + b"\0" * 32 + body[40:]).digest() == geo[8:40], "geometry checksum"
    assert first.read(8192, 4096) == geo, "backup geometry differs"

    hdr = first.read(12288, 256)
    magic, major, minor, hsize = struct.unpack_from("<IHHI", hdr)
    assert magic == LP_HEADER_MAGIC, "no LP metadata header"
    hdr = first.read(12288, hsize)
    assert hashlib.sha256(hdr[:12] + b"\0" * 32 + hdr[44:]).digest() == hdr[12:44], "header checksum"
    tables_size, = struct.unpack_from("<I", hdr, 44)
    tables = first.read(12288 + hsize, tables_size)
    assert hashlib.sha256(tables).digest() == hdr[48:80], "tables checksum"
    desc = [struct.unpack_from("<III", hdr, 80 + i * 12) for i in range(4)]

    def table(i, fmt):
        off, n, size = desc[i]
        return [struct.unpack_from(fmt, tables, off + k * size) for k in range(n)]

    parts = table(0, "<36sIIII")
    extents = table(1, "<QIQI")
    groups = table(2, "<36sIQ")
    bdevs = table(3, "<QIIQ36sI")
    print(f"LP metadata v{major}.{minor}: max size {max_size}, {slots} slots, logical block {lbs}")
    names = []
    for first_sector, align, align_off, size, name, flags in bdevs:
        name = cstr(name)
        names.append(name)
        assert name in devs, f"no super_{name}.img"
        assert devs[name].size == size, f"super_{name}.img is {devs[name].size} bytes, metadata says {size}"
        print(f"  block device {name}: {size} bytes, first logical sector {first_sector}")
    for name, flags, maximum in groups:
        print(f"  group {cstr(name)}: maximum {maximum}")
    assert names[0] == meta_dev, "metadata device is not the first block device"
    for slot in range(1, slots):         # every metadata slot (and the backups) must hold the same tables
        other = first.read(12288 + slot * max_size, hsize + tables_size)
        assert other == hdr + tables, f"metadata slot {slot} differs"

    ok = True
    seen = set()
    for name, attrs, first_ext, n_ext, group in parts:
        name = cstr(name)
        seen.add(name)
        h = hashlib.sha256()
        total = 0
        for num_sectors, ttype, tdata, tsource in extents[first_ext:first_ext + n_ext]:
            assert ttype == 0, f"{name}: non-linear extent"
            dev = devs[names[tsource]]
            off, left = tdata * SECTOR, num_sectors * SECTOR
            assert off >= bdevs[tsource][0] * SECTOR and off + left <= dev.size, f"{name}: extent outside {names[tsource]}"
            total += left
            while left:
                chunk = dev.read(off, min(left, 1 << 24))
                h.update(chunk)
                off += len(chunk)
                left -= len(chunk)
        if name not in images:
            print(f"  partition {name}: {total} bytes (no image to compare)")
            continue
        src, size = hashlib.sha256(), Path(images[name]).stat().st_size
        with open(images[name], "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                src.update(chunk)
        src.update(b"\0" * (total - size))           # partitions are padded up to the extent size
        match = total >= size and src.digest() == h.digest()
        ok &= match
        print(f"  partition {name}: {total} bytes in {n_ext} extent(s), group {cstr(groups[group][0])}, "
              f"readonly={attrs & 1} -> {'matches' if match else 'DIFFERS FROM'} {Path(images[name]).name}")
    missing = set(images) - seen
    if missing:
        ok = False
        print(f"  partitions missing from the metadata: {sorted(missing)}")
    sys.exit(0 if ok else "super image verification FAILED")


if __name__ == "__main__":
    main()
