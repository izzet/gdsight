#!/usr/bin/env bash
# O1 + O2: does better attribution DRIVE a real optimization, and would existing tools have driven it?
# One KV-streaming workload on the real NIXL GDS engine, three KV-cache LAYOUTS:
#   scatter (naive paged KV, sub-4K-misaligned)  -> the status quo
#   aligned (4K-aligned, one read per entry)      -> the "obvious" fix from partial knowledge (O2)
#   packed  (entries contiguous, bulk-coalesced)  -> the fix our per-op device attribution points to (O1)
# For each we record what EVERY observer reports: nvidia-fs aggregate + health (gds_stats-class),
# diskstats (iostat), cuFile-API op count, and OUR per-op LBA (device bytes, A_byte, useful GiB/s).
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-test-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
NA=1; SA=1048576; NB=${NB:-120000}; SB=${SB:-2560}; STRIDE=${STRIDE:-8192}; BATCH=${BATCH:-64}
OUT=/home/cc/projects/gdstrace/results/xlayer; CSV="$OUT/optimization.csv"
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
echo "layout,nvfs_readMiB,nvfs_err,disk_MiB,cufile_api_ops,nvme_cmds,our_deviceB_MiB,our_B_A_byte,useful_GiBps" > "$CSV"
reqA=$((NA*SA)); reqB=$((NB*SB))
for L in scatter aligned packed; do
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  nvfs_b=$(grep -iE '^Reads' /proc/driver/nvidia-fs/stats | head -1)
  disk_b=$(awk '/ nvme1n1 /{print $6}' /proc/diskstats)
  APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na $NA --sa $SA --nb $NB --sb $SB --stride $STRIDE --batch $BATCH --layout $L"
  out=$(datacrumbs_run --app "$APP" 2>&1 | grep -iE 'NIXL_GDS_KV|ERR')
  nvfs_a=$(grep -iE '^Reads' /proc/driver/nvidia-fs/stats | head -1)
  disk_a=$(awk '/ nvme1n1 /{print $6}' /proc/diskstats)
  sleep 1; T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  # aggregate observers
  rb=$(grep -oE 'readMiB=[0-9]+' <<<"$nvfs_b" | tr -dc '0-9'); ra=$(grep -oE 'readMiB=[0-9]+' <<<"$nvfs_a" | tr -dc '0-9')
  nvfs_mib=$(( ${ra:-0} - ${rb:-0} )); nverr=$(grep -oE 'err=[0-9]+' <<<"$nvfs_a" | head -1)
  disk_mib=$(( (${disk_a:-0} - ${disk_b:-0}) * 512 / 1024 / 1024 ))
  apiops=$(zcat "$T" 2>/dev/null | grep -oc cuFileBatchIOSubmit); nvme=$(zcat "$T" 2>/dev/null | grep -oc nvme_setup_cmd)
  # OUR per-op view
  line=$(python3 $HOME/projects/gdstrace/tools/keystone_gds_score.py "$T" "$F" "$reqA" "$reqB" 2>/dev/null)
  devB=$(grep -oE 'B=[0-9.]+MiB' <<<"$line" | tail -1 | tr -dc '0-9.')
  ab=$(grep -oE 'B=[0-9.]+x' <<<"$line" | tail -1 | tr -dc '0-9.')
  gibps=$(grep -oE 'useful_GiBps=[0-9.]+' <<<"$out" | tr -dc '0-9.')
  printf "%-8s  nvfs:%4sMiB err=%-2s | iostat:%4sMiB | cuFileAPI:%-6s ops | nvme:%-7s cmds || OURS: deviceB=%5sMiB  B_A_byte=%-6s  useful=%s GiB/s\n" \
    "$L" "$nvfs_mib" "${nverr#err=}" "$disk_mib" "$apiops" "$nvme" "${devB:-?}" "${ab:-?}" "${gibps:-?}"
  echo "$L,$nvfs_mib,${nverr#err=},$disk_mib,$apiops,$nvme,${devB:-0},${ab:-0},${gibps:-0}" >> "$CSV"
done
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "DONE -> $CSV"; echo; column -t -s, "$CSV"