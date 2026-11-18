#!/usr/bin/env python3
"""Patch the base ROM's org.codeaurora.ims (decoded with `apktool d -r`) so it runs on ColorOS 17.

Usage: patch_ims_app.py <decoded app dir> <TelephonyBaseUtilsStub.smali>

Why this app at all: ColorOS 17's IMS app only speaks the AIDL IImsRadio interface; the Mi 9 vendor image
serves the HIDL one (vendor.qti.hardware.radio.ims@1.6). With ColorOS's app IMS never registers, so there is
no VoLTE, and on the OnePlus 7 Pro port (same situation) outgoing calls failed. The base ROM's app has the
HIDL client. It needs three changes, each found by tools/dex_link_check.py as a reference that exists on the
base ROM's class path but not on ColorOS's (or on neither):

 1. Four classes call a no-argument constructor of their ims-ext-common base class that the platform library
    no longer has; only <init>(Executor, Context) exists, on the base ROM too:
        QtiImsExt -> QtiImsExtBase, CrsCrbtControllerImpl -> CrsCrbtControllerBase,
        ImsMultiIdentityImpl -> ImsMultiIdentityControllerBase,
        ImsScreenShareControllerImpl -> ImsScreenShareControllerBase.
    On the base ROM nothing reaches that code; on ColorOS the phone process binds QtiImsExtService (that is
    the NoSuchMethodError / NullPointerException the OnePlus 7 Pro port hit). Each constructor now calls the
    two-argument one with the context's main executor, exactly as the same classes in ColorOS's own app do.
 2. MediaController calls QtiVideoCallDataUsage.getLteDataUsage(), which ColorOS's ims-ext-common dropped.
    In the base ROM's library that method only forwards to getWwanDataUsage(), which both have.
 3. android.telephony.TelephonyBaseUtilsStub (MIUI helper) is added to the app.

Every edit asserts that the code still looks as expected, so a different base app fails the build instead of
being patched wrongly.
"""
import shutil
import sys
from pathlib import Path


def replace_once(path, old, new):
    text = path.read_text()
    assert text.count(old) == 1, f"{path.name}: expected exactly one occurrence of {old.strip()[:70]!r}"
    path.write_text(text.replace(old, new))


def main():
    app, stub = Path(sys.argv[1]), Path(sys.argv[2])
    smali = app / "smali"

    # (class, its constructor's signature, register holding the Context, base class)
    for cls, ctor, ctx, base in (
            ("QtiImsExt", "(Landroid/content/Context;Ljava/util/List;)V", "p1", "QtiImsExtBase"),
            ("CrsCrbtControllerImpl", "(Lorg/codeaurora/ims/ImsServiceSub;Landroid/content/Context;)V", "p2",
             "CrsCrbtControllerBase"),
            ("ImsMultiIdentityImpl", "(ILorg/codeaurora/ims/ImsSenderRxr;Landroid/content/Context;Landroid/os/Looper;)V",
             "p3", "ImsMultiIdentityControllerBase"),
            ("ImsScreenShareControllerImpl", "(Lorg/codeaurora/ims/ImsServiceSub;Landroid/content/Context;)V", "p2",
             "ImsScreenShareControllerBase")):
        path = smali / f"org/codeaurora/ims/{cls}.smali"
        text = path.read_text()
        start = text.find(f".method public constructor <init>{ctor}\n")
        assert start >= 0, f"{cls}: constructor {ctor} not found"
        body = text[start:text.index(".end method", start)]
        call = f"    invoke-direct {{p0}}, Lorg/codeaurora/ims/{base};-><init>()V\n"
        assert body.count(call) == 1 and text.count(call) == 1, f"{cls}: unexpected super constructor call"
        locals_line = body.split("\n")[1].strip()
        assert locals_line.startswith(".locals ") and int(locals_line.split()[1]) >= 1, f"{cls}: no free register"
        # v0 is not initialised before the super call in any of these constructors, so it is free to use.
        assert "v0" not in body[:body.index(call)], f"{cls}: v0 is used before the super constructor call"
        path.write_text(text.replace(
            call,
            f"    invoke-virtual {{{ctx}}}, Landroid/content/Context;->getMainExecutor()Ljava/util/concurrent/Executor;\n\n"
            f"    move-result-object v0\n\n"
            f"    invoke-direct {{p0, v0, {ctx}}}, Lorg/codeaurora/ims/{base};-><init>"
            f"(Ljava/util/concurrent/Executor;Landroid/content/Context;)V\n"))

    replace_once(smali / "com/qualcomm/ims/vt/MediaController.smali",
                 "Lorg/codeaurora/ims/QtiVideoCallDataUsage;->getLteDataUsage()J",
                 "Lorg/codeaurora/ims/QtiVideoCallDataUsage;->getWwanDataUsage()J")

    dst = smali / "android/telephony/TelephonyBaseUtilsStub.smali"
    assert not dst.exists(), "the app already contains TelephonyBaseUtilsStub"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(stub, dst)
    print("ims app: four base-class constructors, the video data-usage call and the MIUI stub patched")


if __name__ == "__main__":
    main()
