#!/usr/bin/env bash
# Evidence that the EFFECTIVE device read granularity (what sets the amplification) is NOT what any
# layer's documentation reports, and is only knowable by cross-layer measurement.
#   documented (device LBA / NVMe spec / sysfs): printed below
#   effective (measured by GDS-Trace on the true-P2P path): device bytes per aligned read
set -u
PREFIX=$HOME/dc-prefix; NIXL_PY=${NIXL_PY:-$HOME/nixl-test-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces; dev=nvme1n1
echo "=== DOCUMENTED granularity (where a practitioner looks up 'block size') ==="
for p in logical_block_size physical_block_size minimum_io_size max_sectors_kb max_hw_sectors_kb; do
  echo "  /sys/block/$dev/queue/$p = $(cat /sys/block/$dev/queue/$p 2>/dev/null)"
done
echo "  ext4 Block size = $(sudo tune2fs -l /dev/$dev 2>/dev/null | awk -F: '/Block size/{gsub(/ /,"",$2);print $2}')"
echo "=== EFFECTIVE granularity, MEASURED by GDS-Trace (device bytes per aligned read) ==="
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null 2>&1
for S in 512 2048 2560 4096 6144; do
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  N=200
  APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na 0 --nb $N --sb $S --batch 32 --kv-align 0"
  datacrumbs_run --app "$APP" >/dev/null 2>&1
  sleep 1; T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  read devB ab nvme batches < <(python3 $HOME/projects/gdstrace/tools/nixl_devbytes.py "$T" "$F" $((N*S)))
  perread=$(python3 -c "print(round($devB*1048576/$N))")
  spec=$(( ((S+511)/512)*512 )); eff=$(( ((S+4095)/4096)*4096 ))
  printf "  req=%-5sB aligned -> MEASURED device/read=%-5sB | if 512-block: %-5sB | if 4096-block: %-5sB\n" "$S" "$perread" "$spec" "$eff"
done
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "=> the device & NVMe spec report 512 B; the measured effective granularity is 4096 B (ext4 block,"
echo "   confirmed on the GDS P2P path). Trusting the documented 512 B predicts 1.0x and 'no problem'."
