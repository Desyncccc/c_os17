#!/usr/bin/env python3
"""Make a shared library import a different function from the same library.

Usage: elf_swap_import.py patch  <library> <old symbol> <new symbol> <version of the new symbol>
       elf_swap_import.py verify <original> <patched> <old symbol> <new symbol> <version of the new symbol>

The undefined dynamic symbol <old symbol> is renamed to <new symbol> in place (the new name must not be longer)
and its symbol-version entry is pointed at <version>, which the library must already require from the same
file the old symbol came from. Calls through the old symbol's PLT entry then reach the new function. Nothing
moves and the file keeps its size; `patch` writes only the changed bytes, so owner, mode and extended
attributes of the file stay as they are.

`verify` applies the same edit to the original in memory and requires the patched file to be exactly that.

Used for libupdateprof.qti.so: AServiceManager_waitForService (blocks until the service appears, forever if
the vendor image has none) -> AServiceManager_checkService (same signature, returns null at once).
"""
import struct
import sys

DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB, DT_SONAME, DT_RPATH, DT_RUNPATH = 1, 4, 5, 6, 14, 15, 29
DT_GNU_HASH, DT_VERSYM, DT_VERDEF, DT_VERNEED, DT_VERNEEDNUM = 0x6ffffef5, 0x6ffffff0, 0x6ffffffc, 0x6ffffffe, 0x6fffffff
SHT_DYNSYM = 11


def swap(data, old, new, version):
    """Return the edited copy of `data` and a description of what changed."""
    d = bytearray(data)
    assert d[:4] == b"\x7fELF" and d[4] == 2 and d[5] == 1, "not a little-endian ELF64 file"
    old_b, new_b, ver_b = old.encode(), new.encode(), version.encode()
    assert len(new_b) <= len(old_b), "the new name must fit in the old one's place"
    phoff, shoff = struct.unpack_from("<QQ", d, 0x20)
    phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", d, 0x36)
    loads, dynamic = [], None
    for i in range(phnum):
        p_type, _f, p_offset, p_vaddr, _p, p_filesz, _m, _a = struct.unpack_from("<IIQQQQQQ", d, phoff + i * phentsize)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)

    def off(vaddr):
        for v, o, size in loads:
            if v <= vaddr < v + size:
                return o + vaddr - v
        raise AssertionError(f"address {vaddr:#x} is not in a loaded segment")

    def cstr(o):
        return bytes(d[o:d.index(b"\0", o)])

    assert dynamic, "no dynamic section"
    dyn = {}
    str_refs = []                                   # (what, offset into .dynstr) for every user of the string table
    for o in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, val = struct.unpack_from("<QQ", d, o)
        if tag == 0:
            break
        if tag in (DT_NEEDED, DT_SONAME, DT_RPATH, DT_RUNPATH):
            str_refs.append((f"dynamic tag {tag}", val))
        else:
            dyn[tag] = val
    assert DT_VERDEF not in dyn, "library defines symbol versions itself: not handled"
    strtab, symtab = off(dyn[DT_STRTAB]), off(dyn[DT_SYMTAB])
    versym, verneed = off(dyn[DT_VERSYM]), off(dyn[DT_VERNEED])

    nsyms = None
    for i in range(shnum):
        _n, sh_type, _fl, _addr, sh_offset, sh_size, _l, _i, _al, sh_entsize = struct.unpack_from("<IIQQQQIIQQ", d, shoff + i * shentsize)
        if sh_type == SHT_DYNSYM:
            assert sh_offset == symtab and sh_entsize == 24
            nsyms = sh_size // 24
    assert nsyms, "no .dynsym section header"

    index = name_off = None
    for i in range(nsyms):
        st_name, _info, _other, st_shndx = struct.unpack_from("<IBBH", d, symtab + 24 * i)
        if cstr(strtab + st_name) == old_b and st_shndx == 0:
            assert index is None, "symbol imported twice"
            index, name_off = i, st_name
        else:
            str_refs.append((f"symbol {i}", st_name))
    assert index is not None, f"{old} is not an undefined dynamic symbol of this library"
    if DT_GNU_HASH in dyn:                          # undefined symbols come before the hashed ones
        _nb, symoffset = struct.unpack_from("<II", d, off(dyn[DT_GNU_HASH]))
        assert index < symoffset, "symbol is in the GNU hash table"

    # The version the symbol is bound to now, the file it comes from, and the wanted version in that same file.
    cur = struct.unpack_from("<H", d, versym + 2 * index)[0]
    file_of, wanted = {}, {}
    vn = verneed
    for _ in range(dyn[DT_VERNEEDNUM]):
        _ver, vn_cnt, vn_file, vn_aux, vn_next = struct.unpack_from("<HHIII", d, vn)
        str_refs.append(("verneed file", vn_file))
        fname = cstr(strtab + vn_file)
        va = vn + vn_aux
        for _ in range(vn_cnt):
            _hash, _flags, vna_other, vna_name, vna_next = struct.unpack_from("<IHHII", d, va)
            str_refs.append(("verneed version", vna_name))
            file_of[vna_other] = fname
            wanted[(fname, cstr(strtab + vna_name))] = vna_other
            va += vna_next
        vn += vn_next
    assert cur & 0x7fff in file_of, f"{old} has no version requirement"
    lib = file_of[cur & 0x7fff]
    assert (lib, ver_b) in wanted, f"the library does not already require {version} from {lib.decode()}"
    new_ver = wanted[(lib, ver_b)] | (cur & 0x8000)

    # Nothing else may use the bytes of the old name (the linker may share string tails).
    lo, hi = name_off, name_off + len(old_b)
    for what, o in str_refs:
        end = o + len(cstr(strtab + o))
        assert end < lo or o > hi, f"{what} shares the string of {old}"

    d[strtab + lo:strtab + hi + 1] = new_b + b"\0" * (len(old_b) - len(new_b) + 1)
    struct.pack_into("<H", d, versym + 2 * index, new_ver)
    assert len(d) == len(data)
    what = (f"dynamic symbol {index}: {old} -> {new}, version index {cur} -> {new_ver} "
            f"({version} of {lib.decode()})")
    return bytes(d), what


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "patch":
        path = sys.argv[2]
        data = open(path, "rb").read()
        out, what = swap(data, *sys.argv[3:6])
        with open(path, "r+b") as f:
            for i in [i for i in range(len(data)) if data[i] != out[i]]:
                f.seek(i)
                f.write(out[i:i + 1])
        print(f"{path.rsplit('/', 1)[-1]}: {what}; {sum(a != b for a, b in zip(data, out))} bytes changed")
    elif len(sys.argv) == 7 and sys.argv[1] == "verify":
        expect, what = swap(open(sys.argv[2], "rb").read(), *sys.argv[4:7])
        if open(sys.argv[3], "rb").read() != expect:
            sys.exit("the patched file is not the original with exactly this edit")
        print(what)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
