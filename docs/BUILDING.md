# Building

## Host

Ubuntu 24.04 (or Debian 12+), root access, about 25 GB of free disk, a kernel with EROFS and loop devices.
Root is required because the system and vendor trees are copied with their owners, modes and `security.*`
extended attributes, and `mkfs.erofs` writes those into the images.

```bash
sudo ./setup.sh
```

`setup.sh` installs distribution packages and builds or downloads the remaining tools into `./bin` (`lpmake`,
`lpdump`, `secilc` 3.11, `aapt2`, `r8.jar`, `apktool.jar`). Downloads are pinned by SHA-256. The distribution's own
`secilc` 3.5 cannot compile Android 17 policy, and its `mkbootimg` package is incomplete, which is why the
repository carries `tools/bootimg.py`.

## Full build

```bash
sudo ./port.sh
```

Stages, in order: `deps fetch unpack system vendor boot images preflight super package`.
Name stages to run only those, for example:

```bash
sudo ./port.sh images preflight super package
```

`system` and `vendor` patch the trees once and refuse to run twice; run `unpack` again to start from clean
trees (set `KEEP_IMAGES=1` to keep the extracted images so `unpack` does not download again).

| Variable | Default | Meaning |
| --- | --- | --- |
| `WORK` | `./work` | Scratch directory (downloads, trees, intermediate files) |
| `OUT` | `./out` | Images and the package |
| `FBE_MODE` | `standard` | `standard`: plain AOSP file-based encryption. `keep`: the base ROM's wrapped-key and metadata-encryption setup |
| `IMS` | `base` | `base` replaces ColorOS's IMS app with the base ROM's, patched (VoLTE attempt). `donor` keeps ColorOS's |
| `SOUNDTRIGGER` | `remove` | `remove` drops the vendor's SoundTrigger HIDL adapter, `keep` leaves it |
| `OTA_APP` | `remove` | `remove` leaves ColorOS's update client out of the image, `keep` ships it (never install what it offers) |
| `GMS` | `on` | `on` leaves Google Play services enabled on the first boot. `donor` keeps the China build's behaviour: present but disabled until switched on in Settings |
| `BOOTLOG` | `1` | `1` installs the boot log keeper (logs on the cache partition) |
| `EROFS_COMPRESS` | `lz4hc,9` | Compressor for system and vendor; the base kernel reads LZ4 only |
| `KEEP_IMAGES` | `0` | `1` keeps extracted input images after `unpack` |
| `SLIM` | `0` | `1` deletes `system.img`/`vendor.img` once the super images are verified |
| `FIXED_TIME` | build time | Timestamp of every file in the images. Newer than the phone's package cache after a `--keep-data` re-flash, so changed system apps are re-read |
| `R8_JAR` | `./bin/r8.jar` | D8 compiler jar |
| `APKTOOL_JAR` | `./bin/apktool.jar` | Apktool jar (only needed for `IMS=base`) |

## Output

```
out/boot.img  dtbo.img  vbmeta.img  odm.img  system.img  vendor.img
out/super/super_system.img  super_vendor.img  super_cust.img
out/ColorOS17-PLK110-cepheus-<date>.zip   (+ .sha256)
```

The zip holds the images to flash, `flash_all.sh`, `flash_all.bat`, `README.txt` and `SHA256SUMS`.

## Preflight

`tools/preflight.sh` mounts the built images and checks what can be checked without a phone; the `super`
stage then verifies the split super images. A failing check stops the build. The checks and the boot
failures they stand for are listed in [ARCHITECTURE.md](ARCHITECTURE.md).

## GitHub Actions

`.github/workflows/build.yml` runs the same two commands on a hosted runner when started by hand and
uploads the package as a workflow artifact. In a public repository any signed-in GitHub user can download
that artifact until it expires.
