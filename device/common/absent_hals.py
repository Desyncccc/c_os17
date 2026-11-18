#!/usr/bin/env python3
"""Remove framework-manifest declarations of Oplus vendor HALs that nothing serves on a non-Oplus vendor image.

Usage: absent_hals.py <system root> <vendor root> <odm root>

ColorOS's /system/etc/vintf/manifest.xml declares a few vendor.oplus.* HALs that, on OPPO/OnePlus hardware, a
process on the odm partition serves. Their clients follow the usual NDK pattern

    if (AServiceManager_isDeclared(name)) binder = AServiceManager_waitForService(name);

and waitForService has no timeout. With the declaration present and no server, the caller blocks for ever:

    vendor.oplus.hardware.oplusSensor  ISensorFeature     libsensorserviceextimpl.so, a boot hook of the sensor
                                                           service inside system_server
    vendor.oplus.hardware.urcc         IUrcc              liboplus-uah-client.so, loaded by SurfaceFlinger and others
    vendor.oplus.hardware.hummingbird  IOplusHmbirdService  system_server (osense)

Without the declaration the clients get "not declared" at once and carry on without the HAL. (Found and shipped
by the Xiaomi Pad 6 ColorOS 17 port; the OnePlus 7 Pro port did not need it because its vendor serves them.)

A HAL is only removed when neither the vendor nor the odm manifest declares it, i.e. when nothing on the
device side can serve it. The manifest is rewritten in place (inode, owner, mode and label are kept).
"""
import re
import sys
from pathlib import Path

ABSENT = ("vendor.oplus.hardware.oplusSensor", "vendor.oplus.hardware.urcc", "vendor.oplus.hardware.hummingbird")


def declared(paths):
    names = set()
    for p in paths:
        names.update(re.findall(r"<hal\b[^>]*>\s*<name>([^<]+)</name>", p.read_text(errors="replace")))
    return names


def main():
    system, vendor, odm = map(Path, sys.argv[1:4])
    device = [p for d in (vendor / "etc/vintf", odm / "etc/vintf") if d.is_dir() for p in sorted(d.rglob("*.xml"))]
    served = declared(device)
    manifest = system / "system/etc/vintf/manifest.xml"
    text = manifest.read_text()
    removed = []
    for name in ABSENT:
        if name in served:
            print(f"{name}: declared by the device side, kept")
            continue
        pat = re.compile(r"[ \t]*<hal\b[^>]*>\s*<name>%s</name>.*?</hal>[ \t]*\n" % re.escape(name), re.S)
        assert len(pat.findall(text)) == 1, f"expected exactly one declaration of {name} in the system manifest"
        text = pat.sub("", text)
        removed.append(name)
    with open(manifest, "r+") as fh:
        fh.write(text)
        fh.truncate()
    print(f"system manifest: removed declarations of {removed}")


if __name__ == "__main__":
    main()
