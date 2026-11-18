# Status

Build: ColorOS 17 `PLK110_17.0.0.100` (Android 17, security patch 2026-09-01) on the Project Infinity-X 4.2
hardware layer for cepheus. **Not yet booted on a device.**

## Verified on the build host

Every item is a check in `tools/preflight.sh` or `tools/verify_super.py` and runs on each build.

- Both inputs match their published checksums.
- `system.img` and `vendor.img` mount with a mainline EROFS driver, use only the EROFS features of the base
  ROM's own vendor image, and carry the trees' owners, modes, SELinux labels and file capabilities.
- The combined SELinux policy compiles for kernel policy versions 31 and 30, and the way the donor's
  legacy-kernel path compiles it (version 29, `allowx` stripped). Unpatched, the last one fails.
- No SELinux type, property context, service context or keystore context is defined on both sides.
- The ramdisk fstab and the vendor fstab are identical; `/system` is tried as EROFS first; the base ROM's
  `system_ext` and `product` entries are skipped by the donor's `skip_mount.cfg`.
- The boot image differs from the base only in the ramdisk; in the ramdisk only `fstab.qcom` differs.
- The eight replaced network-stack binaries export the same symbols as the ones they cover and their new
  imports resolve against ColorOS's libraries.
- The composer service differs from the original in exactly one instruction.
- All 1,275 native files of the vendor and odm images find every system library and symbol they import in
  ColorOS, compared with the base ROM they were built against (`tools/vendor_link_check.py`). Without the
  `llndk.libraries.txt` fix, 61 of them (camera, among others) would lose `libcamera_metadata.so`.
- The split super images reproduce every logical partition byte for byte and use the base ROM's geometry.
- No HAL that only an Oplus vendor image serves is left declared in the framework manifest.
- `libupdateprof.qti.so` is the donor's file with one import swapped (`waitForService` to `checkService`),
  and ColorOS's `libbinder_ndk.so` exports the new import under the version the library asks for.
- `surfaceflinger` is the donor's file with six `bl` instructions retargeted: every wait for a display HAL
  that no manifest in the images declares. No wait for a declared HAL is touched.
- The schedtune cgroup file, the task profiles and the init commands agree with each other: every group a
  profile joins is created, every controller is mounted, and ColorOS's `libprocessgroup` reads such files.
- The effective display density is 480 after the property files are applied in init's order.
- The replacement IMS app differs from the base ROM's only in its code, carries ColorOS's signing block, and
  every platform class, method and field it references exists on ColorOS's class path
  (`tools/dex_link_check.py`). Unpatched, three references are missing and four constructors do not exist.

## Not verifiable without the phone

Whether it boots, and everything about hardware behaviour. The closest evidence is the OnePlus 7 Pro port:
same donor image, same SoC family, the same kind of custom-ROM base on a 4.14 kernel. Its boot blockers are
the ones handled here. That phone is not this phone: panel, touch, fingerprint, camera and charger are
different parts with different drivers.

## Expected gaps in this build

| Area | Expectation | Why |
| --- | --- | --- |
| In-display fingerprint | Not working | ColorOS drives the sensor light-up through Oplus display HALs; the Mi 9 vendor marks the fingerprint layer by the AOSP layer name. Needs on-device work |
| Camera | Unknown | The donor image has no ColorOS camera app; it ships Open Camera 1.54.1, a plain Camera2 app. Whether ColorOS's camera service works with the Mi 9 camera HAL is untested |
| Calls, VoLTE | Attempted, untested | ColorOS's IMS app cannot talk to this vendor's HIDL IMS radio, so the base ROM's app is shipped instead (`IMS=base`). It links cleanly; whether IMS registers on your network is unknown. If telephony misbehaves, build with `IMS=donor` to get ColorOS's app back (then expect no VoLTE and, as on the OnePlus 7 Pro port, failing outgoing calls) |
| VoWiFi, video calls | Unknown | Same app; not looked at separately |
| Auto brightness | Unknown, likely needs tuning | The donor overlay's brightness tables describe the OnePlus 15 panel and are not carried over, so the framework defaults apply |
| Voice wake-up | Not working | SoundTrigger service removed on purpose |
| Wireless charging UI, reverse charging | Removed | Oplus charger HAL not present; charging itself is handled by the kernel |
| Charging notices | Possibly wrong warnings | ColorOS reads charger state from an Oplus HAL this vendor lacks. The Pad 6 port saw a "battery may not be genuine" dialog until it added a stand-in for that HAL, and a "charging voltage too high" notice with third-party chargers. Not addressed here |
| Game GPU control panel | Removed | Needs Qualcomm's QSPM service, which this vendor image does not have |
| Always-on display, screen-off gestures, haptics tuning | Unknown | Depend on Oplus touch and display HALs |
| About phone | Shows `PLK110` as model | Model and fingerprints keep the donor values because ColorOS features key on them |
| Google apps | Play services only | The donor is the China build: it carries Google Play services and the services framework but no Play Store, and disables them on the first boot. This build leaves them enabled (`GMS=on`); the Settings switch remains. The Play Store has to be installed by hand |
| OTA updates | Updater removed | ColorOS's update client (`com.oplus.ota`) would offer OnePlus 15 firmware and system-app bundles, which can replace apps this port changed (the IMS app, for one). It is left out (`OTA_APP=remove`); Settings then shows no software-update entry. Updating means flashing a newer build of this port |

## Next steps once a phone is available

1. Flash, capture the three log sources in [DEBUGGING.md](DEBUGGING.md), fix what stops the boot.
2. Confirm the basics: display, touch, Wi-Fi, mobile data, calls, SMS, audio, sensors.
3. Fingerprint: provide the display-panel feature the ColorOS framework asks for and mark the pressed layer
   for the Mi 9 kernel's HBM path.
4. Camera: try Open Camera first. If the camera service works but the app does not suit the sensors, the
   base ROM's camera app (Aperture, `/product/app/Aperture`) is a drop-in candidate: it is unprivileged,
   carries its native libraries inside the APK, and `tools/dex_link_check.py` finds every platform
   reference it makes on ColorOS's class path.
5. Hardened build: adb authorisation, `ro.debuggable=0`, enforcing SELinux with a domain for the helpers.
