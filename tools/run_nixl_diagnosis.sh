#!/usr/bin/env bash
# DIAGNOSIS WALKTHROUGH: a KV-offload pipeline that current tools call "healthy" but wastes 2/3 of its
# device read bandwidth. Shows the fix a practitioner derives from current tools (add concurrency)
# is futile, while the fix our per-op attribution prescribes (fix KV read alignment / coalesce) works.
# All KV-only (na=0), nb=2000 x 2560B (Llama-3.1-70B PP8). For each arm we report what EACH observer sees:
#   (current tools) nvidia-fs aggregate GDS MiB + iostat device MiB  -> "healthy / device busy"
#   (ours)          per-op LBA device bytes for the KV class + A_byte -> the amplification, localized
#   (outcome)       goodput (useful MiB/s delivered to GPU)
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-test-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
NB=${NB:-2000}; SB=${SB:-2560}; reqB=$((NB*SB))
OUT=/home/cc/projects/gdstrace/results/xlayer; CSV="$OUT/nixl_diagnosis.csv"
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
printf "arm,fix_source,batch,align,coalesce,nvfs_MiB,iostat_MiB,our_devB_MiB,A_byte,goodput_MiBps\n" > "$CSV"

run_arm() { # label fix_source batch align coalesce
  local label=$1 src=$2 batch=$3 align=$4 coal=$5
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  local nvfs0 sec0; nvfs0=$(grep -m1 '^Reads' /proc/driver/nvidia-fs/stats | grep -oE 'readMiB=[0-9]+' | tr -dc 0-9)
  sec0=$(awk '$3=="nvme1n1"{print $6}' /proc/diskstats)
  local APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na 0 --nb $NB --sb $SB --batch $batch --kv-align $align --kv-coalesce $coal"
  local out gp; out=$(datacrumbs_run --app "$APP" 2>&1)
  gp=$(grep -oE 'goodput=[0-9.]+' <<<"$out" | tr -dc '0-9.')
  local nvfs1 sec1; nvfs1=$(grep -m1 '^Reads' /proc/driver/nvidia-fs/stats | grep -oE 'readMiB=[0-9]+' | tr -dc 0-9)
  sec1=$(awk '$3=="nvme1n1"{print $6}' /proc/diskstats)
  local nvfsMiB=$(( nvfs1 - nvfs0 )); local ioMiB=$(( (sec1-sec0)*512/1024/1024 ))
  sleep 1; local T; T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  read devB ab nvme batches devB_exact < <(python3 $HOME/projects/gdstrace/tools/nixl_devbytes.py "$T" "$F" "$reqB")
  printf "%-16s | tool:%-8s batch=%-3s align=%-4s coal=%-5s | CURRENT TOOLS: nvfs=%sMiB iostat=%sMiB | OURS: devB=%sMiB A_byte=%-5s | goodput=%sMiB/s\n" \
    "$label" "$src" "$batch" "$align" "$coal" "$nvfsMiB" "$ioMiB" "$devB" "$ab" "${gp:-?}"
  echo "$label,$src,$batch,$align,$coal,$nvfsMiB,$ioMiB,$devB,$ab,${gp:-0}" >> "$CSV"
}

echo "reqB (useful KV bytes) = $(awk "BEGIN{printf \"%.2f\",$reqB/1048576}") MiB"
run_arm "BASELINE"          "--"      64  3072 0
run_arm "OBVIOUS:batch2x"   "gds_stats/iostat" 128 3072 0
run_arm "OURS:align4K"      "GDSight" 64 0    0
run_arm "OURS:coalesce"     "GDSight" 64 0    65536
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "DONE -> $CSV"
