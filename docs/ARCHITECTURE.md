# Architecture and rationale

The port puts a ColorOS 17 system on top of a maintained Android 17 custom-ROM base for the Mi 9. The base
owns everything that talks to hardware; ColorOS owns the framework and apps. This is the same split the
OnePlus 7 Pro port uses, and for the same reason: Android 17 does not run on the stock 4.14 kernel, and the
kernel that does (eBPF backports) only matches the custom ROM's vendor image.

## Inputs

| Input | File | Check |
| --- | --- | --- |
| Base | `Project_Infinity-X-4.2-cepheus-09.11.2026-VANILLA-UNOFFICIAL.zip` | SHA-256 pinned in `config/cepheus.env` |
| Donor | `ColorOS-17-PLK110-1789230075256-18092026-AxisGSI.zip` | MD5 as published in the project's file feed |

Both are built from the same AOSP release (`CP2A.260605.016`), so the vendor interface version (202604),
the SELinux mapping and the VINTF level line up without translation.

Facts read from the images (see `port.sh unpack` and the analysis that led to the scripts):

- Base kernel `4.14.357-openela-perf`: EROFS with LZ4, pstore, userfaultfd, blk-crypto with fallback,
  `CONFIG_SECURITY_SELINUX_DEVELOP=y`. The base ROM sets `ro.bpf.kver_override=5.10.239`.
- Boot image header v2, gzip ramdisk holding first-stage init and `system/etc/fstab.qcom`.
  Command line: `androidboot.super_partition=system`, `androidboot.init_fatal_reboot_target=recovery`.
- Retrofit super: block devices `system` 3758096384, `vendor` 1610612736, `cust` 1073741824 bytes; group
  `qti_dynamic_partitions` 6438256640 bytes; metadata on `system`.
- `vbmeta` already has verity and verification disabled (flags 3).
- Donor: ext4 GSI, 5.9 GiB; `/system_ext`, `/product` and the `my_*` trees are directories of the system
  image; `skip_mount.cfg` keeps first-stage init from mounting the base ROM's `system_ext`/`product`.
- The donor's init never switches SELinux to enforcing, so with this kernel the port runs permissive. The
  policy still has to compile and load.

## Boot path and what can stop it

| Step | Failure seen on the reference port | What this port does | Checked by |
| --- | --- | --- | --- |
| First-stage mount | `/system` is EROFS, fstab says ext4 | EROFS entry added in front of the ext4 one in the ramdisk fstab and the vendor fstab (`tools/patch_fstab.py`) | preflight: both fstabs identical, entry order and flags |
| SELinux policy compile | The GSI init caps the policy to version 29 on a 4.14 kernel and strips only `allowx`; `auditallowx`/`dontauditx` and the xperm rule in the vendor's `plat_pub_versioned.cil` then fail the compile: init fatal | Those logging-only rules are removed on both sides (`tools/selinux_port.py`) | preflight compiles the policy for versions 31, 30 and 29-with-`allowx`-stripped using secilc 3.11 |
| SELinux name clashes | Vendor types that ColorOS declares as attributes | Computed and renamed/dropped; for the Mi 9 vendor there are none | preflight |
| Property contexts | Same property name with two contexts aborts property-area setup | Clashing system-side entries dropped; for the Mi 9 vendor there are none | preflight (also checks for any duplicate name) |
| eBPF loader | ColorOS's tethering APEX refuses kernel 4.14 and ignores `ro.bpf.kver_override`: `reboot,netbpfload-failed` | The base ROM's override-aware builds of 8 native files and the eBPF programs are bind-mounted over the APEX just before the loader runs; `ro.bpf.kver_override` set. Each file must export the same symbols as the one it covers (`tools/elf_compat.py`) | preflight |
| `/data` | ColorOS vold vs the base ROM's encryption setup | Plain AOSP file-based encryption (`aes-256-xts:aes-256-cts:v2`), no wrapped keys, no metadata encryption; installer erases `userdata` and `metadata` | preflight |
| Audio HAL | Crash loop through the SoundTrigger callback took SurfaceFlinger and system_server down | SoundTrigger HIDL adapter and its manifest entry removed (`SOUNDTRIGGER=keep` to undo) | preflight |
| First frame | ColorOS SurfaceFlinger sends QTI and Oplus composer commands the AOSP composer service rejects: stuck on the splash screen | One conditional branch in the composer service is replaced by a NOP so unknown commands are skipped (`tools/patch_composer.py`) | preflight: exactly four bytes differ from the original |

## System-side changes (`device/cepheus/adapt_system.py`, `port.sh system`)

- `debug.stagefright.ccodec=4`: the vendor image serves video through Codec2 only (`vendor.media.omx=0`).
- `persist.vendor.radio.poweron_opt=0`: with 1, qcrild buffers incoming SMS waiting for an acknowledgement
  ColorOS never sends.
- `CUST_LEVEL_LIST` exported from `init.environ.rc`, otherwise ColorOS ignores every `my_*` feature file.
- `llndk.libraries.txt`: libraries the base ROM exposes to vendor code and ColorOS ships
  (`libcamera_metadata.so`, used by 61 vendor files) are added so vendor HALs keep linking. Preflight then
  checks every vendor ELF file against ColorOS's libraries (`tools/vendor_link_check.py`).
- `ro.oplus.storage.super_size` set and `supersize.rc` removed (its script can leave a value that crashes
  the wallpaper app).
- OnePlus 15 hardware declarations removed or corrected: punch-hole position, QHD resolution switch,
  LTPO/adaptive refresh, under-screen light-sensor compensation, 5G, always-on sensor hub, wireless and
  reverse charging features.
- Display overlays rebuilt for the Mi 9 (`device/cepheus/overlay`, `tools/build_overlays.sh`): 1080x2340,
  the notch outline and status-bar height, transparent corner masks. The donor's colour-mode list is kept
  because ColorOS crashes without it.
- Battery profile (3300 mAh) converted from the base ROM's compiled overlay to
  `my_product/etc/power_profile/power_profile.xml`.
- `00-port-oplus-stubs.rc` + `oplus-port-stubs.dex` (`device/common/stubs`): stand-ins for
  `IOplusProject` and `IPerformance`, whose absence costs a 5 s wait on every lookup.
- `00-port-bootlog.rc` + `port-bootlog.sh`: boot logs on the cache partition (`BOOTLOG=0` to leave out).

## Calls and VoLTE (`IMS=base`, `port.sh` `ims_swap`)

ColorOS 17's IMS app (`org.codeaurora.ims`) only contains the AIDL client for the modem's IMS interface. The
Mi 9 vendor image serves the HIDL one (`vendor.qti.hardware.radio.ims@1.6`). With ColorOS's app IMS never
registers: no VoLTE, and on the OnePlus 7 Pro port, which was in the same position, outgoing calls failed
until the app was replaced. Where 3G is switched off, calls depend on VoLTE or a 2G fallback.

The port ships the base ROM's IMS app, which has the HIDL client and runs on this vendor image today:

- `device/common/ims/patch_ims_app.py` edits the decoded app (apktool, code only): four constructors that
  call a no-argument base-class constructor the platform library no longer has now call the
  `(Executor, Context)` one, as ColorOS's own app does; one call to a method ColorOS's library dropped is
  redirected to the method it forwarded to; a MIUI helper class the base ROM supplies from a separate jar is
  compiled in.
- `tools/dex_link_check.py` is how those were found and how the result is checked: every class, method and
  field the app references outside itself is resolved on the base ROM's class path and on ColorOS's. After
  patching nothing is missing and no constructor is left dangling. The three shared libraries the app uses
  have the same classes on both platforms.
- `tools/apk_sigblock.py` attaches the signing block of ColorOS's IMS app, so the replacement presents
  ColorOS's certificate to the other telephony components. The package manager does not check content
  digests for APKs on system partitions.
- The app's two JNI libraries come from the base ROM and sit in the app's own `lib/arm64`; their imports
  resolve in ColorOS's platform libraries.

None of this has run on a phone. `IMS=donor` builds with ColorOS's app untouched.

The Axis GSI hides `/vendor/overlay` at boot and uses its own copies of the donor's vendor overlays. That is
kept: the Mi 9's own framework overlay would replace the colour-mode list ColorOS depends on.

## Images and packaging

- `system.img` and `vendor.img` are EROFS, LZ4HC, built from trees that carry owners, modes, SELinux
  labels and file capabilities as extended attributes. The build refuses any EROFS feature beyond the set
  the base ROM's own vendor image uses, since that set is known to mount on this kernel.
- `odm.img`, `dtbo.img` and `vbmeta.img` are the base ROM's, unchanged.
- `boot.img`: `tools/bootimg.py` swaps the ramdisk and recomputes the image id; `tools/cpio_edit.py`
  replaces the one fstab file inside the ramdisk archive and leaves every other entry byte-identical.
- Super: `lpmake` with the base ROM's geometry writes one sparse image per block device.
  `tools/verify_super.py` parses the metadata with its own code, follows every extent across the devices
  and compares the SHA-256 with the partition images.
- With the current sizes no logical partition reaches `cust`, so it is not flashed.
