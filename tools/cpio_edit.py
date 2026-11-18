#!/usr/bin/env python3
"""Read or replace one file inside a "newc" cpio archive (an Android ramdisk) without unpacking it.

  cpio_edit.py get <archive> <member path> <output file>
  cpio_edit.py put <archive> <member path> <input file> <new archive>
  cpio_edit.py list <archive>

Every other entry (order, mode, owner, timestamps, device nodes) is copied byte for byte, which a
cpio -i / cpio -o round trip does not guarantee. Only single-archive ramdisks are supported.
"""
import sys

MAGIC = b"070701"
FIELDS = ("ino", "mode", "uid", "gid", "nlink", "mtime", "filesize", "devmajor", "devminor",
          "rdevmajor", "rdevminor", "namesize", "check")


def pad4(n):
    return (4 - n % 4) % 4


def parse(data):
    """Yield (header dict, name, file data) for every entry including the trailer; return trailing bytes."""
    pos, entries = 0, []
    while True:
        assert data[pos:pos + 6] == MAGIC, f"not a newc cpio header at offset {pos}"
        pos_hdr = pos
        vals = [int(data[pos + 6 + i * 8: pos + 14 + i * 8], 16) for i in range(13)]
        hdr = dict(zip(FIELDS, vals))
        name_start = pos + 110
        name = data[name_start: name_start + hdr["namesize"] - 1].decode()
        data_start = name_start + hdr["namesize"] + pad4(110 + hdr["namesize"])
        body = data[data_start: data_start + hdr["filesize"]]
        pos = data_start + hdr["filesize"] + pad4(hdr["filesize"])
        hdr["_raw"] = data[pos_hdr: pos_hdr + 110]
        entries.append((hdr, name, body))
        if name == "TRAILER!!!":
            break
    rest = data[pos:]
    assert not rest.strip(b"\0"), "data after the cpio trailer (concatenated archives are not supported)"
    return entries, rest


def build(entries, rest_len):
    out = bytearray()
    for hdr, name, body in entries:
        # Reuse the original header bytes; only the file size field (7th of 13) can change.
        raw_hdr = bytearray(hdr["_raw"])
        if len(body) != hdr["filesize"]:
            lower = raw_hdr[6:].lower() == raw_hdr[6:]
            raw_hdr[54:62] = (("%08x" if lower else "%08X") % len(body)).encode()
        out += raw_hdr
        raw = name.encode() + b"\0"
        out += raw + b"\0" * pad4(110 + len(raw))
        out += body + b"\0" * pad4(len(body))
    # Keep the archive padded the same way (cpio pads to a 512-byte block).
    block = 512
    out += b"\0" * ((block - len(out) % block) % block)
    return bytes(out)


def main():
    cmd, archive = sys.argv[1], sys.argv[2]
    entries, rest = parse(open(archive, "rb").read())
    if cmd == "list":
        for hdr, name, body in entries:
            print("%06o %5d %5d %9d %s" % (hdr["mode"], hdr["uid"], hdr["gid"], len(body), name))
        return
    member = sys.argv[3].lstrip("./")
    hits = [i for i, (_, name, _) in enumerate(entries) if name.lstrip("./") == member]
    assert len(hits) == 1, f"{member}: found {len(hits)} times in {archive}"
    if cmd == "get":
        open(sys.argv[4], "wb").write(entries[hits[0]][2])
    elif cmd == "put":
        hdr, name, _ = entries[hits[0]]
        entries[hits[0]] = (hdr, name, open(sys.argv[4], "rb").read())
        open(sys.argv[5], "wb").write(build(entries, len(rest)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
