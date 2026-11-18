#!/system/bin/sh
# cepheus port (experimental builds): boot log keeper, started by 00-port-bootlog.rc at post-fs.
# Logs go to <cache partition>/port-logs: logcat.txt and kmsg.txt for this boot, prev-* for the one before.
# The ColorOS root has /cache as a link into /data, so the cache partition is otherwise unused here.
# Overridable for testing: PORT_LOG_DEV (block device), PORT_LOG_DIR (mount point).
DEV=${PORT_LOG_DEV:-/dev/block/bootdevice/by-name/cache}
D=${PORT_LOG_DIR:-/mnt/port_log}
mkdir -p "$D" || exit 0
mount -t ext4 -o noatime,nosuid,nodev "$DEV" "$D" 2>/dev/null \
  || mount -t f2fs -o noatime,nosuid,nodev "$DEV" "$D" 2>/dev/null || exit 0
mkdir -p "$D/port-logs" && cd "$D/port-logs" || exit 0
for f in logcat.txt logcat.txt.1 kmsg.txt; do
  rm -f "prev-$f"
  [ -f "$f" ] && mv -f "$f" "prev-$f"
done
# Kernel log, capped at 32 MiB per boot; logcat keeps at most two 16 MiB files.
( dmesg -w 2>/dev/null | head -c 33554432 > kmsg.txt ) &
exec logcat -b all -v threadtime -f logcat.txt -r 16384 -n 1
