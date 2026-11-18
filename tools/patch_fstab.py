#!/usr/bin/env python3
"""Adapt the base ROM's fstab (boot ramdisk copy and /vendor/etc copy) for the ColorOS EROFS system image.

Usage: patch_fstab.py <fstab file> <fbe mode: standard | keep>

1. /system: an `erofs` entry is inserted in front of the existing ext4 one. fs_mgr tries the entries of one
   mount point in order, so an ext4 system (the base ROM, any ext4 GSI) still mounts with the same boot image.
2. /data (mode "standard"): the base ROM uses Qualcomm hardware-wrapped keys
   (fileencryption=...+inlinecrypt_optimized+wrappedkey_v0, metadata_encryption=aes-256-xts:wrappedkey_v0).
   That path needs vold/keystore/keymaster to agree on wrapped storage keys; ColorOS's vold was never tested
   against this device's Keymaster 4.1 HAL. "standard" switches to plain AOSP file-based encryption
   (aes-256-xts:aes-256-cts:v2, software fscrypt, no dm-default-key), the configuration with the fewest moving
   parts and the one the OnePlus 7 Pro port booted with on the same kernel generation.
   Changing the encryption policy requires formatted userdata and metadata partitions.
   Mode "keep" leaves the /data entry untouched.

The file is rewritten in place so its SELinux label, owner and mode are kept.
"""
import re
import sys


def main():
    path, mode = sys.argv[1], sys.argv[2]
    assert mode in ("standard", "keep"), mode
    lines = open(path).read().split("\n")
    out, saw_system, saw_data = [], False, False
    for line in lines:
        f = line.split()
        if len(f) >= 5 and not line.lstrip().startswith("#") and f[1] == "/system":
            if f[2] == "erofs":
                raise SystemExit(f"{path}: already has an erofs /system entry")
            assert f[2] == "ext4", f"unexpected /system filesystem {f[2]}"
            out.append("%-55s %-22s %-7s %-52s %s" % (f[0], f[1], "erofs", "ro", f[4]))
            saw_system = True
        if len(f) >= 5 and not line.lstrip().startswith("#") and f[1] == "/data" and mode == "standard":
            mnt = ",".join(o for o in f[3].split(",") if o != "inlinecrypt")
            flags = []
            for o in f[4].split(","):
                if o.startswith("fileencryption="):
                    o = "fileencryption=aes-256-xts:aes-256-cts:v2"
                elif o.startswith(("metadata_encryption=", "keydirectory=")):
                    continue
                flags.append(o)
            assert any(o.startswith("fileencryption=") for o in flags), "no fileencryption= on /data"
            line = "%-55s %-22s %-7s %-52s %s" % (f[0], f[1], f[2], mnt, ",".join(flags))
            saw_data = True
        out.append(line)
    assert saw_system, f"{path}: no ext4 /system entry found"
    assert saw_data or mode == "keep", f"{path}: no /data entry found"
    with open(path, "r+") as fh:
        fh.write("\n".join(out))
        fh.truncate()
    for line in out:
        if re.search(r"\s/(system|data)\s", line) and not line.lstrip().startswith("#"):
            print("  " + " ".join(line.split()))


if __name__ == "__main__":
    main()
