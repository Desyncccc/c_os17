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
| system_server start | Clients that find an Oplus HAL declared in the framework manifest wait for it; three declared HALs have no server on a non-Oplus vendor | The three declarations are removed unless the vendor serves them (`device/common/absent_hals.py`) | preflight |
| Shortly after boot | A Qualcomm GPU-profile library waits for a vendor service with no timeout; on the Xiaomi Pad 6 port this froze the UI until the watchdog restarted it | The library imports the non-blocking lookup instead (`tools/elf_swap_import.py`); the feature that offers the control panel is removed | preflight: the file is the original with exactly that edit |
| In use (colour mode, sharpness, Game Filter) | SurfaceFlinger's main thread waits for Oplus/Pixelworks display HALs with no timeout: frozen screen on the Pad 6 port | The waits for HALs no manifest declares call the non-blocking lookup (`tools/patch_sf_waits.py`) | preflight: the file is the original with only those `bl` instructions changed |

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
- `ro.sf.lcd_density=480` in `/product/etc/build.prop`. ColorOS sets 480 in `my_product`, but that file is
  loaded with the system properties and the vendor image's 440 comes later and wins; `/product` is loaded
  last. ColorOS's display-size steps and the rebuilt overlays assume 480 on a 1080-wide panel.
- Wired headset detection needs no change: the GSI's own start-up script enables its
  input-event overlay on Qualcomm hardware.
- The update client `/system_ext/app/OTA` (`com.oplus.ota`) is removed (`OTA_APP=remove`). The phone
  identifies as a OnePlus 15, so the client would fetch that model's firmware, which does not fit this
  phone's partition layout, and system-app bundles, which install like any app update: an Oplus-signed update of
  `org.codeaurora.ims` would replace the patched IMS app. The Pad 6 port removes the same app and reports
  that Settings then hides the entry.
- Google Play services (`GMS=on`): the China build reads
  `my_product/etc/config/sys_gms_download_app_whitelist.xml` on the first boot and, with
  `<switch value="disable" />`, disables the package. The switch is set to `enable`; the user-facing toggle
  in Settings is untouched. The first boot decides, so this only takes effect on a clean flash.
- Battery profile (3300 mAh) converted from the base ROM's compiled overlay to
  `my_product/etc/power_profile/power_profile.xml`.
- `00-port-oplus-stubs.rc` + `oplus-port-stubs.dex` (`device/common/stubs`): stand-ins for
  `IOplusProject` and `IPerformance`, whose absence costs a 5 s wait on every lookup.
- `00-port-bootlog.rc` + `port-bootlog.sh`: boot logs on the cache partition (`BOOTLOG=0` to leave out).

## Services this vendor image does not have

A ColorOS system on a non-Oplus vendor image must never block on a service that will not appear. Lookups
with a timeout only cost time; lookups without one stop the calling thread, and when that thread belongs to
system_server its watchdog restarts the UI or the phone. The cases below come from the Xiaomi Pad 6 port
(`pipa`), which hit them on a device with the same class of vendor image.

- **Declared but unserved HALs.** The framework manifest (`/system/etc/vintf/manifest.xml`) declares
  `vendor.oplus.hardware.oplusSensor`, `vendor.oplus.hardware.urcc` and `vendor.oplus.hardware.hummingbird`,
  but no binary in the system image registers them; on an Oplus phone they sit in the vendor image. Client
  libraries such as `libsensorserviceextimpl.so` (inside system_server's sensor service) and
  `liboplus-uah-client.so` ask "is it declared?" and, if so, wait.
  `device/common/absent_hals.py` removes each declaration unless the vendor or odm manifest serves it.
- **GPU profiles.** `GameGpuControlPanel` in system_server pushes per-game GPU driver profiles through
  `libupdateprof.qti.so`, which calls `AServiceManager_waitForService` for Qualcomm's Snapdragon Services /
  QSPM HAL. The Mi 9 vendor has neither. `tools/elf_swap_import.py` renames that one import to
  `AServiceManager_checkService` (same signature, returns null at once) and points its symbol version at
  `LIBBINDER_NDK`, which the library already requires; 15 bytes change, nothing moves. The library's own
  "no service" path then runs. The earlier ColorOS build the Pad 6 port fell back to imports
  `checkService` in the same place. The feature `oplus.gpu.controlpanel.support` is removed as well, since
  the panel cannot do anything here.
- **SurfaceFlinger.** ColorOS's SurfaceFlinger has six `AServiceManager_waitForService` calls. Each is
  followed by the typed `fromBinder` conversion, which identifies the interface: Oplus
  `IDisplayColorFeature` (twice: colour-space update, sharpness), Oplus `IMixLut3D` (Game Filter look-up
  tables), Pixelworks `IIris` and `IIrisFeature`, and AOSP's AIDL composer. The three Oplus ones have no
  "is it declared?" test in front of them and run on SurfaceFlinger's main thread, so a call would freeze
  the screen for good. `tools/patch_sf_waits.py` finds the calls through the PLT, reads the interface from
  the conversion that follows, and redirects a call to `AServiceManager_checkService` (already imported by
  SurfaceFlinger) only if no VINTF manifest of the build declares that HAL. On this vendor image that is
  all six; the Pixelworks and composer ones sit behind a declaration test and were never reached anyway.
  A wait for a HAL the device does declare is kept, because that service will appear. The code after each
  call logs "can not get ..." and carries on when the result is null. The binary on the Pad 6 port has the
  same six calls, and that port runs with the Oplus and `IIris` ones redirected.
- **Already safe, checked on every build:** `libtrigger-handler.so` (app-launch hints to QSPM) only runs
  when `ro.vendor.qspm.enable` is `true`, which nothing sets. The Oplus engineer service waits for its HAL
  only when `ro.adb.secure` is 1; the donor has 0 and it has to stay that way until that code is patched.

## Scheduler groups

The kernel is a 4.14 EAS kernel with `CONFIG_SCHED_TUNE` and no utilisation clamping. The vendor image
tunes `/dev/stune/{top-app,foreground,background}`. AOSP supports such devices through files chosen by
`ro.product.first_api_level` (28 from the vendor image): `/system/etc/task_profiles/cgroups_28.json` mounts
the `schedtune` controller and `task_profiles_28.json` moves the standard task profiles onto it. The base
ROM has both and creates the groups in `init.rc`. ColorOS ships neither, so `/dev/stune` would not exist:
the vendor's tuning writes would fail and no task would ever be placed in the groups that tuning is for.

`port.sh` (`sched_groups`) installs the base ROM's cgroup file, a task-profile file built by
`tools/make_task_profiles.py` (each profile keeps ColorOS's `cpu` group and gains the base ROM's
`schedtune` group, so ColorOS's own CPU-share handling is unchanged), and `00-port-stune.rc` with the base
ROM's group-creation commands. Files are written for API level 28 and for the donor's own value (36), so the
result does not depend on which of the two property definitions init ends up with.

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
