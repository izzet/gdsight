#!/usr/bin/env bash
# run_write_sweep.sh -- GDS write path: does misalignment cost, and does GDS stay engaged?
#
# The nvidia-fs "Writes" counter is the cleanest "did GDS actually engage" signal: it is GDS-specific,
# so co-tenant traffic on the shared NVMe cannot pollute it (unlike /proc/diskstats). Compare it against
# the app-level op count gdsio reports: gds_ops << app_ops means cuFile silently served the writes off
# the GDS path.
#
# Needs: rw_stats_enabled=1 (per boot), else every nvidia-fs counter reads 0. See CLAUDE.md.
# Usage: tools/run_write_sweep.sh [file] [size]
set -uo pipefail
GDSIO=${GDSIO:-/usr/local/cuda-12.6/gds/tools/gdsio}
F=${1:-/mnt/nvme1/gdstrace-smoke/wtest.bin}
SZ=${2:-64M}

[ "$(cat /sys/module/nvidia_fs/parameters/rw_stats_enabled 2>/dev/null)" = 1 ] || {
  echo "WARN: nvidia_fs rw_stats_enabled != 1 -> counters read 0." >&2
  echo "      echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled" >&2; }
[ -f "$F" ] || { echo "staging $SZ target with dd (GDS writes are safe, but create files with dd)";
                 dd if=/dev/zero of="$F" bs=1M count=$(( ${SZ%M} )) oflag=direct status=none; sync; }

nw(){ awk '/^Writes/{gsub("n=","",$3); print $3; exit}' /proc/driver/nvidia-fs/stats; }

printf '%-8s %-10s %10s %10s %14s\n' size align gds_ops app_ops throughput
for sz in 4K 16K 64K 1M; do
  for al in aligned unaligned; do
    U=""; [ "$al" = unaligned ] && U="-U"
    sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
    a=$(nw)
    out=$("$GDSIO" -f "$F" -d 0 -w 1 -s "$SZ" -i $sz -I 3 -x 0 $U 2>&1 | tail -1)
    sync; b=$(nw)
    printf '%-8s %-10s %10s %10s %14s\n' "$sz" "$al" "$((b-a))" \
      "$(sed -n 's/.*ops: \([0-9]*\).*/\1/p' <<<"$out")" \
      "$(sed -n 's/.*Throughput: \([0-9.]*\).*/\1/p' <<<"$out") GiB/s"
  done
done
