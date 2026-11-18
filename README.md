# ColorOS 17 for the Xiaomi Mi 9 (cepheus)

Build scripts that port ColorOS 17 (Android 17, OnePlus 15 `PLK110` system image) to the Xiaomi Mi 9.
The repository holds scripts, small source files and documentation only. No firmware is stored here:
`port.sh` downloads two public ROMs, verifies their checksums and assembles a flashable package.

## Status: experimental, not booted on a phone yet

The package builds from the two pinned inputs and passes every off-device check in `tools/preflight.sh`,
but nobody has flashed it. A ROM port normally needs several rounds of flashing and reading logs before everything
works, and those rounds need the phone. Treat the first flash as a test, keep a backup and a way back.

| | |
| --- | --- |
| Verified on the build host | Images build and mount; SELinux policy compiles the three ways init may compile it; property contexts merge; fstab, super layout and boot image are consistent; super images match the partition images byte for byte |
| Expected from the same donor on the same SoC family (OnePlus 7 Pro port) | Boots to the ColorOS UI; display, touch, Wi-Fi, Bluetooth, mobile data, audio |
| Unknown until tested on a Mi 9 | Everything above, on this phone |
| Attempted, untested | VoLTE and calls: the base ROM's IMS app replaces ColorOS's (build option `IMS`) |
| Expected not to work in this build | In-display fingerprint, the ColorOS camera app, wireless-charging UI, voice wake-up, NFC payments |

Details: [docs/STATUS.md](docs/STATUS.md).

## How it is put together

| Layer | Source | What the port changes |
| --- | --- | --- |
| Kernel, boot ramdisk, dtbo, vbmeta | Project Infinity-X 4.2 for cepheus (Android 17; 4.14.357 kernel with eBPF backports) | First-stage fstab gets an EROFS `/system` entry and plain file-based encryption for `/data`. Kernel, dtb and command line are untouched |
| `vendor`, `odm` | Same base ROM | fstab as above; one instruction in the display composer service so unknown composer commands are skipped; SoundTrigger HIDL adapter removed; one SELinux rule dropped |
| `system` (with `system_ext`, `product`, `my_*`) | ColorOS 17 `PLK110`, Axis GSI build | SELinux and property fixes, network-stack binaries for the 4.14 kernel, the base ROM's IMS app (patched) for VoLTE, Mi 9 display overlays and hardware properties, schedtune groups for the 4.14 scheduler, endless waits for services this vendor lacks removed (framework manifest, a GPU-profile library, SurfaceFlinger), two helper services |
| Partition layout | The base ROM's retrofit super (`system` + `vendor` + `cust`, 6 GiB) | Logical partitions `system`, `vendor`, `odm`; about 1.5 GiB stays free |

Why each change exists: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Build

Ubuntu 24.04, root, about 25 GB of free disk and 15 minutes:

```bash
sudo ./setup.sh
```

```bash
sudo ./port.sh
```

The package is written to `out/ColorOS17-PLK110-cepheus-<date>.zip`. Options and single stages are
described in [docs/BUILDING.md](docs/BUILDING.md). The same build can run in GitHub Actions
(`.github/workflows/build.yml`, manual trigger); note that in a public repository the resulting artifact
is downloadable by any signed-in GitHub user while it is retained.

## Flash

Requirements: unlocked bootloader, the phone already running a retrofit dynamic-partition Android 17 ROM
(Project Infinity-X 4.x), a full backup. Flashing erases userdata including internal storage.

Unpack the package, reboot the phone to the bootloader and run `flash_all.sh` (Linux/macOS) or
`flash_all.bat` (Windows). It writes `boot`, `dtbo`, `vbmeta`, `system` and `vendor`, erases `userdata`,
`metadata` and `frp`, and leaves the bootloader, modem and every other firmware partition alone.

If it does not boot, collect logs as described in [docs/DEBUGGING.md](docs/DEBUGGING.md). To go back,
flash the base ROM from its recovery and format data.

## Security note

The donor image is a debug-style build: adb is enabled without authorisation, `ro.debuggable=1` and SELinux
runs permissive. That is what makes a first bring-up debuggable, and it is not suitable for a phone that
holds sensitive accounts or data.

## Credits

- [Project Infinity-X](https://projectinfinity-x.com/) and the cepheus maintainer for the Android 17 base
  (kernel, vendor, retrofit layout).
- The Axis GSI project for the ColorOS 17 `PLK110` system image.
- The [OnePlus 7 Pro ColorOS 17 port](https://github.com/nathanatgit/oneplus7-coloros17-port): its notes
  document, with on-device evidence, the failures a ColorOS 17 system hits on an SM8150 vendor with a
  4.14 kernel. The fixes here follow those findings; the code is written for this repository.
- The [Xiaomi Pad 6 ColorOS 17 port](https://github.com/Stefan-Atanasov/pipa-cos17-port): its notes on
  system_server waiting for services a Xiaomi vendor image does not have led to the fixes described under
  "Services this vendor image does not have" in the architecture notes.
- [nmeum/android-tools](https://github.com/nmeum/android-tools) and the
  [SELinux project](https://github.com/SELinuxProject/selinux) for the host tools.

ColorOS, OxygenOS and all OPPO, OnePlus and Xiaomi firmware belong to their owners. This project is not
affiliated with any of them. Flashing can brick a phone and erases its data; you do it at your own risk.
