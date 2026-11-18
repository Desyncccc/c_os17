ColorOS 17 (PLK110) port for the Xiaomi Mi 9 (cepheus) - EXPERIMENTAL
======================================================================

This build has NOT been booted on a phone. It was assembled and statically checked on a build server.
Expect it to need fixes; keep a way back (see "Going back").

Before you flash
  * Unlocked bootloader.
  * The phone must already be on a retrofit dynamic-partition setup with Android 17 firmware, i.e.
    Project Infinity-X 4.x for cepheus (or another retrofit ROM) booted at least once. The port reuses
    that ROM's kernel, vendor and partition layout (super = system + vendor + cust).
  * Back up everything. Flashing ERASES userdata, including internal storage.
  * Android platform-tools (fastboot) on a PC.

Flash
  1. Reboot the phone to the bootloader (power off, hold Volume Down + Power).
  2. Run flash_all.sh (Linux/macOS) or flash_all.bat (Windows) from this folder.
  3. First boot formats userdata; allow 5-10 minutes. The system language starts as Chinese; the setup
     wizard lets you choose English.

What is flashed: boot, dtbo, vbmeta, and the super members system and vendor (cust, the third member,
only if the package contains super_cust.img). userdata and metadata are erased. Bootloader, modem and
all other firmware partitions are left alone.

If it does not boot
  * Back in recovery after ~20 s: init hit a fatal error (the boot image sets
    androidboot.init_fatal_reboot_target=recovery).
  * Stuck on the Mi logo or boot animation: connect USB. This build has adb enabled without authorisation
    during boot (debug build):
        adb logcat -b all > logcat.txt
        adb shell dmesg > dmesg.txt
  * After a crash/reboot, boot the recovery and pull the previous kernel log:
        adb pull /sys/fs/pstore
  * Send those logs with the build date to the repository's issue tracker; see docs/DEBUGGING.md there.

Going back
  Flash the base ROM (Project Infinity-X) zip from its recovery, then format data. Nothing outside the
  partitions listed above was changed.

Security note
  This is a debug-style build: adb is on, ro.adb.secure=0 and SELinux is permissive. Do not use it with
  sensitive accounts or data until a hardened build exists.
