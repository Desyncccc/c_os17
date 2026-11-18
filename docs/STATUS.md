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
| ColorOS camera app | Likely crashes | Built for the OnePlus 15 camera HAL. Third-party Camera2 apps are the first thing to try |
| Calls, VoLTE | Attempted, untested | ColorOS's IMS app cannot talk to this vendor's HIDL IMS radio, so the base ROM's app is shipped instead (`IMS=base`). It links cleanly; whether IMS registers on your network is unknown. If telephony misbehaves, build with `IMS=donor` to get ColorOS's app back (then expect no VoLTE and, as on the OnePlus 7 Pro port, failing outgoing calls) |
| VoWiFi, video calls | Unknown | Same app; not looked at separately |
| Auto brightness curve | Off | The donor's nit tables are for another panel |
| Voice wake-up | Not working | SoundTrigger service removed on purpose |
| Wireless charging UI, reverse charging | Removed | Oplus charger HAL not present; charging itself is handled by the kernel |
| Always-on display, screen-off gestures, haptics tuning | Unknown | Depend on Oplus touch and display HALs |
| About phone | Shows `PLK110` as model | Model and fingerprints keep the donor values because ColorOS features key on them |
| Google apps | None | The donor is the China build |
| OTA updates | Must not be installed | The updater would write OnePlus 15 firmware layouts |

## Next steps once a phone is available

1. Flash, capture the three log sources in [DEBUGGING.md](DEBUGGING.md), fix what stops the boot.
2. Confirm the basics: display, touch, Wi-Fi, mobile data, calls, SMS, audio, sensors.
3. Fingerprint: provide the display-panel feature the ColorOS framework asks for and mark the pressed layer
   for the Mi 9 kernel's HBM path.
4. Camera: stock app or a port of the base ROM's.
5. Hardened build: adb authorisation, `ro.debuggable=0`, enforcing SELinux with a domain for the helpers.
