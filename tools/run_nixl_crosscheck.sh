#!/usr/bin/env bash
# THE irreplaceability cross-check: on ONE real-NIXL GDS run (Llama-3.1-70B KV pattern), capture what
# every AGGREGATE observer sees vs what our per-op cross-layer tool sees. Shows that the per-class
# device amplification (small KV reads) is invisible/un-localizable to aggregate tools and
# un-attributable to timing (batch corr_id collapse) -- only per-NVMe-command LBA recovers it.
#
# Observers captured around the SAME run:
#   (A) nvidia-fs /proc/driver/nvidia-fs/stats   = what gds_stats-class tools report (aggregate GDS)
#   (B) /proc/diskstats nvme1n1                  = what iostat reports (aggregate device)
#   (C) our eBPF trace                           = per-op cuFile<->nvfs<->NVMe (LBA per class + corr_id)
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
NA=${NA:-200}; SA=${SA:-1048576}; NB=${NB:-2000}; SB=${SB:-2560}; BATCH=${BATCH:-64}
OUT=/home/cc/projects/gdstrace/results/xlayer; mkdir -p "$OUT"
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null

dev=nvme1n1
nvfs_before=$(cat /proc/driver/nvidia-fs/stats 2>/dev/null)
disk_before=$(grep " $dev " /proc/diskstats 2>/dev/null)

APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na $NA --sa $SA --nb $NB --sb $SB --batch $BATCH"
echo "=== running real NIXL GDS workload (traced) ==="
datacrumbs_run --app "$APP" 2>&1 | grep -iE 'NIXL_GDS_KV|ERR' | head

nvfs_after=$(cat /proc/driver/nvidia-fs/stats 2>/dev/null)
disk_after=$(grep " $dev " /proc/diskstats 2>/dev/null)
sleep 2
T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)

echo "=== raw aggregate counters (before -> after) ===" | tee "$OUT/nixl_crosscheck.txt"
{
echo "--- nvidia-fs /proc stats (Read lines) ---"
echo "BEFORE:"; echo "$nvfs_before" | grep -iE 'read' | head
echo "AFTER:";  echo "$nvfs_after"  | grep -iE 'read' | head
echo "--- diskstats $dev (field6=sectors read; *512=bytes) ---"
echo "BEFORE: $disk_before"; echo "AFTER:  $disk_after"
} | tee -a "$OUT/nixl_crosscheck.txt"

echo "=== OUR per-op cross-layer view ===" | tee -a "$OUT/nixl_crosscheck.txt"
python3 $HOME/projects/gdstrace/tools/keystone_gds_score.py "$T" "$F" 2>/dev/null | tee -a "$OUT/nixl_crosscheck.txt"
echo "cuFileBatchIOSubmit events (cuFile-API layer): $(zcat "$T" 2>/dev/null | grep -oc cuFileBatchIOSubmit)" | tee -a "$OUT/nixl_crosscheck.txt"
echo "nvfs_io=$(zcat "$T" 2>/dev/null|grep -oc nvfs_io) p2p=$(zcat "$T" 2>/dev/null|grep -oc nvfs_get_p2p_dma_mapping) nvme=$(zcat "$T" 2>/dev/null|grep -oc nvme_setup_cmd)" | tee -a "$OUT/nixl_crosscheck.txt"
echo "$T" > /tmp/nixl_last_trace.txt
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "DONE -> $OUT/nixl_crosscheck.txt"
