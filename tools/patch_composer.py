#!/usr/bin/env python3
"""Make the AOSP HIDL composer service (android.hardware.graphics.composer@2.4-service) skip commands it cannot
parse instead of failing the whole command buffer.

ColorOS 17's SurfaceFlinger writes commands the stock AOSP command engine does not know into the composer command
stream: the Qualcomm QtiExtension SET_LAYER_TYPE (0x800 << 16) and SET_LAYER_GENERIC_METADATA carrying Oplus
layer metadata. The engine's loop is

    while (!isEmpty()) {
        if (!beginCommand(&command, &length)) break;
        bool parsed = executeCommand(command, length);
        endCommand();                 // re-synchronises the read pointer to the end of the command
        if (!parsed) { ALOGE("failed to parse command 0x%x, length %u", ...); break; }
    }
    if (!isEmpty()) return Error::BAD_PARAMETER;

so one unknown command rejects the frame: nothing is presented and the phone stays on the boot splash (seen on
the OnePlus 7 Pro port, which uses the same composer service). Because endCommand() already moves the read
pointer past the command, removing the `if (!parsed) break` is enough to skip unknown commands.

The patch is located by structure, not by offset: the `adrp/add` pair that loads the "failed to parse command"
format string marks the error block, and exactly one `tbz w0, #0, <error block>` must branch to it. That
instruction is replaced by a NOP. The file is edited in place (inode, mode and xattrs are kept).

Usage: patch_composer.py <composer service binary> [--check]
"""
import struct
import sys

NOP = 0xD503201F
MSG = b"failed to parse command 0x%x, length %u"


def load_segments(d):
    assert d[:4] == b"\x7fELF" and d[4] == 2 and d[5] == 1, "not a little-endian ELF64 file"
    assert struct.unpack_from("<H", d, 18)[0] == 183, "not an AArch64 binary"
    phoff, = struct.unpack_from("<Q", d, 32)
    phentsize, phnum = struct.unpack_from("<HH", d, 54)
    segs = []
    for i in range(phnum):
        p_type, p_flags, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from("<IIQQQQQQ", d, phoff + i * phentsize)
        if p_type == 1:
            segs.append((p_vaddr, p_offset, p_filesz, p_flags))
    return segs


def off2va(segs, off):
    for va, o, sz, _ in segs:
        if o <= off < o + sz:
            return va + off - o
    raise SystemExit("file offset not in a loadable segment")


def main():
    path = sys.argv[1]
    check_only = "--check" in sys.argv[2:]
    d = bytearray(open(path, "rb").read())
    segs = load_segments(d)
    assert d.count(MSG) == 1, "expected exactly one 'failed to parse command' string"
    msg_va = off2va(segs, d.index(MSG))

    # Find `adrp x2, page(msg)` followed by `add x2, x2, #pageoff(msg)` in an executable segment.
    blocks = []
    for va, off, sz, flags in segs:
        if not flags & 1:
            continue
        for i in range(0, sz - 4, 4):
            ins, nxt = struct.unpack_from("<II", d, off + i)
            if ins & 0x9F00001F != 0x90000002:        # adrp x2
                continue
            immlo, immhi = (ins >> 29) & 3, (ins >> 5) & 0x7FFFF
            imm = (immhi << 2) | immlo
            if imm & (1 << 20):
                imm -= 1 << 21
            page = ((va + i) & ~0xFFF) + (imm << 12)
            if page != msg_va & ~0xFFF:
                continue
            if nxt & 0xFFC003FF != 0x91000042:        # add x2, x2, #imm12
                continue
            if (nxt >> 10) & 0xFFF == msg_va & 0xFFF:
                blocks.append((va + i, off, va))
    assert len(blocks) == 1, f"expected one reference to the error string, found {len(blocks)}"
    target, seg_off, seg_va = blocks[0]

    # Find the single `tbz w0, #0, target` (the `if (!parsed)` test) and any already-patched state.
    hits = []
    for va, off, sz, flags in segs:
        if not flags & 1:
            continue
        for i in range(0, sz, 4):
            ins, = struct.unpack_from("<I", d, off + i)
            if ins & 0xFFF8001F != 0x36000000:        # tbz w0, #0, <label>
                continue
            imm14 = (ins >> 5) & 0x3FFF
            if imm14 & (1 << 13):
                imm14 -= 1 << 14
            if va + i + imm14 * 4 == target:
                hits.append(off + i)
    if not hits:
        # Already patched? Then no branch reaches the error block any more; accept only our own NOP.
        raise SystemExit("no `tbz w0, #0` branch to the parse-error block: binary already patched or layout unknown")
    assert len(hits) == 1, f"expected one branch to the parse-error block, found {len(hits)}"
    where = hits[0]
    print(f"parse-error block at 0x{target:x}; `if (!parsed) break` at file offset 0x{where:x}")
    if check_only:
        return
    with open(path, "r+b") as fh:
        fh.seek(where)
        fh.write(struct.pack("<I", NOP))
    print("patched: unknown composer commands are now skipped")


if __name__ == "__main__":
    main()
