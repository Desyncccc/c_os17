#!/usr/bin/env python3
"""Xiaomi Mi 9 (cepheus) adjustments to the ColorOS 17 PLK110 (OnePlus 15) system tree.

Usage: adapt_system.py <system root> <ro.bpf.kver_override value> <llndk.libraries.txt of the base ROM>

Every edit rewrites the existing file in place (same inode: owner, mode and SELinux label are kept) and asserts
that the donor still looks the way the edit expects; the donor image is pinned by checksum, so a failing assert
means the donor was changed and the edit has to be reviewed.

The donor describes OnePlus 15 hardware. What differs on the Mi 9:
  display   1080x2340 fixed 60 Hz AMOLED with a waterdrop notch (donor: 1272x2772 LTPO 1-165 Hz, punch hole)
  sensors   one front light sensor, no always-on sensor hub
  radio     LTE only (SM8150 + X24), no 5G
  charging  27 W wired; wireless charging exists but is driven by Xiaomi's charger stack, not Oplus's
  battery   3300 mAh
"""
import re
import sys
from pathlib import Path

S = Path(sys.argv[1])
KVER = sys.argv[2]
BASE_LLNDK = Path(sys.argv[3])


def rewrite(path, text):
    with open(path, "r+", encoding="utf-8") as fh:
        fh.write(text)
        fh.truncate()


class Props:
    def __init__(self, rel):
        self.path = S / rel
        self.text = self.path.read_text(encoding="utf-8")

    def _pat(self, key):
        return re.compile(r"^%s=.*$" % re.escape(key), re.M)

    def set(self, key, value, count=None):
        """Replace the value of an existing key (all definitions; `count` asserts how many exist)."""
        n = len(self._pat(key).findall(self.text))
        assert n >= 1 and (count is None or n == count), f"{self.path.name}: {key} defined {n} times"
        self.text = self._pat(key).sub(f"{key}={value}", self.text)

    def drop(self, key):
        pat = re.compile(r"^%s=.*\n" % re.escape(key), re.M)
        assert pat.search(self.text), f"{self.path.name}: {key} not found"
        self.text = pat.sub("", self.text)

    def add(self, comment, lines):
        for line in lines:
            key = line.split("=", 1)[0]
            assert not self._pat(key).search(self.text), f"{self.path.name}: {key} already defined"
        if not self.text.endswith("\n"):
            self.text += "\n"
        self.text += f"\n# cepheus port: {comment}\n" + "\n".join(lines) + "\n"

    def save(self):
        rewrite(self.path, self.text)


def drop_features(rel, tag, names):
    path = S / rel
    text = path.read_text(encoding="utf-8")
    for name in names:
        pat = re.compile(r'[ \t]*<%s name="%s"[^>]*?/>[ \t]*\n' % (tag, re.escape(name)), re.S)
        assert len(pat.findall(text)) == 1, f"{path.name}: feature {name}"
        text = pat.sub("", text)
    rewrite(path, text)
    print(f"{rel}: removed {len(names)} features")


# ---- /system/build.prop ---------------------------------------------------------------------------------
p = Props("system/build.prop")
# Video codecs: the donor's 1 means "Codec2 for audio, OMX for video". The Mi 9 vendor image turns OMX off
# (vendor.media.omx=0) and serves everything through its Codec2 HAL, which the base ROM uses with the
# default 4 (Codec2 for everything). With 1 only software video decoding would be left.
p.set("debug.stagefright.ccodec", "4", count=1)
p.add("the base kernel (4.14) has the eBPF features of android12-5.10 backported; the base ROM's network "
      "stack reads this override (see system/etc/init/00-port-netbpfload.rc)",
      [f"ro.bpf.kver_override={KVER}"])
p.add("port identification", ["ro.port.device=cepheus", "ro.port.base=Project Infinity-X 4.2 (Android 17)"])
p.save()

# ---- /system_ext/etc/build.prop -------------------------------------------------------------------------
p = Props("system_ext/etc/build.prop")
# With poweron_opt=1 qcrild buffers incoming SMS and waits for an acknowledgement the ColorOS telephony stack
# never sends: no text message arrives (found on the OnePlus 7 Pro port, same qcrild generation).
p.set("persist.vendor.radio.poweron_opt", "0", count=1)
# supersize.rc runs getsuperlayoutsize.sh (lpdump | awk | bc) at boot_completed and can leave "\r" in this
# property, which crashes the wallpaper app's property parser. Set the real size of the retrofit super
# (system 3758096384 + vendor 1610612736 + cust 1073741824) and remove the script's trigger.
p.add("size of the retrofit super partition (system + vendor + cust)", ["ro.oplus.storage.super_size=6442450944"])
p.save()
(S / "system_ext/etc/init/supersize.rc").unlink()

# ---- /my_product/build.prop -----------------------------------------------------------------------------
p = Props("my_product/build.prop")
p.drop("ro.oplus.display.screenhole.positon")            # no punch hole
for key in ("ro.oplus.resolution.low", "ro.oplus.resolution.high", "ro.oplus.density.qhd_default",
            "ro.density.screenzoom.qdh"):                # FHD+ only: no resolution switch
    p.drop(key)
p.set("ro.sf.lcd_density", "480")                        # = ro.oplus.density.fhd_default (the vendor image sets 440)
p.set("persist.oplus.display.vrr", "0")                  # fixed 60 Hz panel: no LTPO / adaptive frame rate
p.set("persist.oplus.display.vrr.adfr", "0")
p.set("persist.oplus.display.vrr.pdfr", "0")
p.drop("persist.oplus.display.ogfr.exclusive")
p.set("ro.display.underscreen.lightsensor.support", "0")  # no Oplus under-screen light sensor compensation
p.set("ro.oplus.radio.dual_nr_enabled", "false")
p.set("ro.oplus.radio.dual_nr_toggle_enabled", "false")
p.add("typical battery capacity shown in Settings > About device", ["ro.product.batterycapacity=3300"])
p.save()

drop_features("my_product/etc/extension/com.oplus.oplus-feature.xml", "oplus-feature", [
    "oplus.software.aon_sensorhub_enable", "oplus.software.aon_phone_camera_gesture_recognition",
    "oplus.software.aon_aod_enable", "oplus.software.display.adfr_v32_lp",
    "oplus.hardware.wired_reverse_charging", "oplus.software.display.camera.brighten",
    "oplus.software.display.camera.curve", "oplus.software.radio.support_5g",
    "oplus.software.radio.support_dual_nr"])
drop_features("my_product/etc/extension/com.oplus.app-features.xml", "app_feature", [
    "os.charge.settings.wirelesscharging.power", "os.charge.settings.wirelesscharge.support",
    "oplus.power.onwirelesscharger.support", "oplus.power.wirelesschgwhenwired.support",
    "os.charge.settings.wirelesschargingcoil.position"])
drop_features("my_product/etc/extension/feature_com.android.battery.xml", "app_feature",
              ["oplus.power.onwirelesscharger.support"])
# Adreno GPU control panel (per-game driver profiles through Qualcomm's QSPM HAL, which this vendor lacks).
drop_features("my_product/etc/permissions/oplus.product.features_gameeco_common.xml", "oplus-feature",
              ["oplus.gpu.controlpanel.support"])

# ---- /my_manifest/build.prop ----------------------------------------------------------------------------
# Only the marketing names shown in Settings change. Model, device, fingerprints and OTA keys keep the donor
# values: ColorOS features and Oplus services key on them. Never accept an OTA inside the port.
p = Props("my_manifest/build.prop")
p.set("ro.vendor.oplus.market.name", "Xiaomi Mi 9")
p.set("ro.vendor.oplus.market.enname", "Xiaomi Mi 9", count=1)
p.save()

# ---- /init.environ.rc -----------------------------------------------------------------------------------
# ColorOS finds its per-partition configuration (<root>/etc/extension features, permissions, ...) through
# CUST_LEVEL_LIST. Stock devices export it from the boot ramdisk; a GSI has no such ramdisk, so without this
# line every my_* feature file is ignored. Order as decoded from a stock OTA by the OnePlus 7 Pro port.
env = S / "init.environ.rc"
text = env.read_text()
assert "CUST_LEVEL_LIST" not in text and text.rstrip().endswith("#END_EUCLID_PATH"), "init.environ.rc changed"
rewrite(env, text.rstrip("\n") + "\n    export CUST_LEVEL_LIST /system:/system_ext:/vendor:/odm:/odm/cust:"
        "/my_preload:/my_stock:/my_region/global:/my_product:/my_region:/my_bigball:/my_carrier:"
        "/my_carrier/cust:/my_company:/my_engineering:/my_product/special\n")

# ---- /system/etc/llndk.libraries.txt --------------------------------------------------------------------
# linkerconfig builds the vendor namespace from this list. Libraries the base ROM exposes to its vendor
# image as LLNDK must stay visible when ColorOS provides them, or vendor HALs fail to link
# (libcamera_metadata.so -> camera provider crash on the OnePlus 7 Pro port).
llndk = S / "system/etc/llndk.libraries.txt"
have = llndk.read_text().split()
added = []
for lib in BASE_LLNDK.read_text().split():
    if lib not in have and ((S / "system/lib64" / lib).exists() or (S / "system/lib" / lib).exists()):
        added.append(lib)
if added:
    rewrite(llndk, "\n".join(have + added) + "\n")
print(f"llndk.libraries.txt: added {added}")
print("cepheus system adjustments applied")
