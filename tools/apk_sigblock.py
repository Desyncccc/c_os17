#!/usr/bin/env python3
"""Read or transplant the APK Signing Block (signature schemes v2/v3).

  apk_sigblock.py info <apk>
  apk_sigblock.py transplant <donor apk> <unsigned, zipaligned apk> <output apk>

A system app that replaces one of ColorOS's own has to present ColorOS's signing certificate: permissions
and bindings between the platform's telephony components are granted by signature. The package manager scans
APKs on system partitions without checking content digests (it only reads the certificates out of the
signing block), so the donor APK's block attached to the replacement gives it the same identity. This is how
the OnePlus 7 Pro port ships its patched system apps. It does not work for APKs installed to /data.

zipalign must run before the transplant: it rewrites the archive and would drop the block.
"""
import hashlib
import struct
import sys

MAGIC = b"APK Sig Block 42"


def eocd(data):
    pos = data.rfind(b"PK\x05\x06")
    assert pos >= 0, "no ZIP end-of-central-directory record"
    cd_size, cd_off = struct.unpack_from("<II", data, pos + 12)
    assert cd_off + cd_size == pos, "unexpected data between the central directory and its end record"
    return pos, cd_off


def block(data):
    """(start offset, block bytes) of the signing block, or (central directory offset, b'') if none."""
    _, cd_off = eocd(data)
    if data[cd_off - 16:cd_off] != MAGIC:
        return cd_off, b""
    size, = struct.unpack_from("<Q", data, cd_off - 24)
    start = cd_off - size - 8
    assert struct.unpack_from("<Q", data, start)[0] == size, "inconsistent signing block size fields"
    return start, data[start:cd_off]


def schemes(blk):
    ids, pos, end = [], 8, len(blk) - 24
    while pos < end:
        n, ident = struct.unpack_from("<QI", blk, pos)
        ids.append(ident)
        pos += 8 + n
    names = {0x7109871A: "v2", 0xF05368C0: "v3", 0x1B93AD61: "v3.1", 0x42726577: "padding"}
    return [names.get(i, hex(i)) for i in ids]


def main():
    cmd = sys.argv[1]
    if cmd == "info":
        data = open(sys.argv[2], "rb").read()
        start, blk = block(data)
        if not blk:
            print("no APK Signing Block")
            sys.exit(1)
        print(f"APK Signing Block: {len(blk)} bytes at offset {start}, entries {schemes(blk)}, "
              f"sha256 {hashlib.sha256(blk).hexdigest()}")
    elif cmd == "transplant":
        donor = open(sys.argv[2], "rb").read()
        target = open(sys.argv[3], "rb").read()
        _, blk = block(donor)
        assert blk, "the donor APK has no APK Signing Block"
        cd_off, old = block(target)
        assert not old, "the target APK already has a signing block"
        end, cd_start = eocd(target)
        out = bytearray(target[:cd_start] + blk + target[cd_start:])
        struct.pack_into("<I", out, end + len(blk) + 16, cd_start + len(blk))
        open(sys.argv[4], "wb").write(out)
        check_start, check = block(bytes(out))
        assert check == blk and check_start == cd_start, "transplant verification failed"
        print(f"signing block transplanted: {len(blk)} bytes, entries {schemes(blk)}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
