#!/system/bin/sh
# cepheus port (experimental builds): boot log keeper, started by 00-port-bootlog.rc at post-fs.
# Logs go to <cache partition>/port-logs: logcat.txt and kmsg.txt for this boot, prev-* for the one before.
# The ColorOS root has /cache as a link into /data, so the cache partition is otherwise unused here.
D=/mnt/port_log
mkdir -p $D || exit 0
mount -t ext4 -o noatime,nosuid,nodev /dev/block/bootdevice/by-name/cache $D || exit 0
mkdir -p $D/port-logs && cd $D/port-logs || exit 0
for f in logcat.txt logcat.txt.1 kmsg.txt; do
  [ -f $f ] && mv -f $f prev-$f
done
dmesg -w > kmsg.txt 2>/dev/null &
exec logcat -b all -v threadtime -f logcat.txt -r 16384 -n 1
