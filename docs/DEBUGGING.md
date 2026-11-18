# When it does not boot

The build has not run on a phone, so the first flash is a test. This page is the loop for turning a failed
boot into a fix.

## What the symptom tells you

| Symptom | Likely stage | First thing to get |
| --- | --- | --- |
| Back in recovery about 20 s after the Mi logo | init fatal error (policy, properties, first-stage mount); the boot image sets `androidboot.init_fatal_reboot_target=recovery` | pstore kernel log |
| Reboots after 1-2 minutes | `netbpfload` failure, `/data` encryption failure, or a critical service crash-looping | boot logs on the cache partition, pstore |
| Stays on the Mi logo | SurfaceFlinger or the composer never presents a frame | `adb logcat`, look for `composer`, `SurfaceFlinger`, `failed to parse command` |
| Boot animation forever | system_server crash loop or watchdog | `adb logcat -b all`, look for `FATAL EXCEPTION IN SYSTEM PROCESS`, `Watchdog` |

## Getting logs

1. **Live over USB.** adb is on without authorisation in this build. As soon as the device shows up:

   ```bash
   adb logcat -b all > logcat.txt
   ```

   ```bash
   adb shell dmesg > dmesg.txt
   ```

2. **After the fact, from recovery** (`BOOTLOG=1`, the default). Each boot writes `logcat.txt` and
   `kmsg.txt` to `port-logs/` on the cache partition and keeps the previous boot as `prev-*`. Boot the
   recovery, mount Cache, then:

   ```bash
   adb pull /cache/port-logs
   ```

   The logger starts at `post-fs`, so it does not cover first-stage init or the policy load.

3. **Kernel log of the boot that just died** (covers the early part too). The kernel has pstore; from
   recovery:

   ```bash
   adb pull /sys/fs/pstore
   ```

   This only works if the recovery's own kernel has pstore enabled.

## Things to try, cheapest first

- **Rule out the base.** Flash the base ROM's `boot.img`, `vendor` and `system` back (its zip from
  recovery). If that boots, the hardware layer is fine and the problem is on the ColorOS side.
- **Encryption.** If the log shows `enablefilecrypto_failed`, `Failed to install fscrypt key` or keystore
  errors while mounting `/data`: rebuild with `FBE_MODE=keep` (the base ROM's wrapped-key setup), flash
  boot and vendor again, erase `userdata` and `metadata`.
- **Audio HAL crash loop** with `SOUNDTRIGGER=keep`: go back to the default `remove`.
- **Calls, VoLTE.** In `adb logcat -b all` look for `ImsRadioHidl` (the app found the modem's IMS interface),
  `MMTEL` feature state, `FATAL EXCEPTION` in `org.codeaurora.ims` or `com.android.phone`, and
  `NoSuchMethodError` / `NoClassDefFoundError`. `adb shell dumpsys telephony.registry | grep -i ims` shows
  the registration state. If the replacement app crashes or takes the phone process down, rebuild with
  `IMS=donor` and re-flash; switching the IMS app changes its user id, so do that with a full flash (data
  erased), not `--keep-data`.
- **Policy.** `SELinux: ... failed` or `secilc` errors in the kernel log: run
  `tools/sepolicy_compile_test.sh` against the mounted images with the message's policy version.
- **A vendor HAL that will not start.** With SELinux permissive the usual causes are a missing library in
  the vendor linker namespace (`CANNOT LINK EXECUTABLE` in logcat; check `system/etc/llndk.libraries.txt`)
  or a missing VINTF entry.
- **A system service waiting 5 s for an Oplus HAL** (`AServiceManager_getService`, `waitForService` on a
  `vendor.oplus.hardware.*` name in the ANR trace): if the caller copes with empty results, add the name to
  `device/common/init/00-port-oplus-stubs.rc`. Do not stub interfaces that return arrays or structures.

## Changing something on the phone before rebuilding

`/vendor` and `/system` are read-only EROFS, so a change means a rebuild. To test a fix first, bind-mount a
patched file over the original from a root adb shell (`mount -o bind`), restart the affected service, and
only then put the change into the scripts with a matching preflight check.

## Reporting

Include the package name (build date), the options used, the base ROM version on the phone, and the three
log sources above.
