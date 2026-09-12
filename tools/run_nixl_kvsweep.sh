#!/usr/bin/env bash
# Thoroughness: drive the REAL NIXL GDS engine across actual model-parallel KV I/O sizes and show
# the per-op device read-amplification (only our LBA attribution sees it) + corr_id collapse, for each.
# KV sizes are real NIXL inference_workload_matgen outputs for Llama-3.1-70B at different
# tensor/pipeline-parallel configs (block-access) and the layer-access size:
#   256 B  = layer-access ;  1280 B = PP16 ;  2560 B = PP8 ;  10240 B = PP2 block-access
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
NA=10; SA=1048576; NB=${NB:-2000}; BATCH=${BATCH:-64}
OUT=/home/cc/projects/gdstrace/results/xlayer; CSV="$OUT/nixl_kvsweep.csv"
echo "kv_size_B,model_parallel,reqB_MiB,deviceB_MiB,A_byte,corr_id_correct_pct,corr_id_unattr_pct,batches" > "$CSV"
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
declare -A LABEL=( [256]="layer-access" [1280]="PP16" [2560]="PP8" [10240]="PP2" )
for SB in 256 1280 2560 10240; do
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na $NA --sa $SA --nb $NB --sb $SB --batch $BATCH"
  datacrumbs_run --app "$APP" >/tmp/kvsw_$SB.out 2>&1
  sleep 1
  T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  reqA=$((NA*SA)); reqB=$((NB*SB))
  line=$(python3 $HOME/projects/gdstrace/tools/keystone_gds_score.py "$T" "$F" "$reqA" "$reqB" 2>/dev/null)
  ab=$(grep -oE 'B=[0-9.]+x' <<<"$line" | tail -1 | tr -dc '0-9.')
  cc=$(grep -oE '[0-9]+% correct' <<<"$line" | tr -dc '0-9')
  un=$(grep -oE '[0-9]+% unattributed' <<<"$line" | tr -dc '0-9')
  devB=$(grep -oE 'B=[0-9.]+MiB' <<<"$line" | tail -1 | tr -dc '0-9.')
  batches=$(zcat "$T" 2>/dev/null | grep -oc cuFileBatchIOSubmit)
  printf "%-6s %-12s reqB=%6.2fMiB deviceB=%6.2fMiB  A_byte=%-6s  corr_id: %s%% correct / %s%% unattr  batches=%s\n" \
    "$SB" "${LABEL[$SB]}" "$(awk "BEGIN{print $reqB/1048576}")" "${devB:-0}" "${ab:-?}" "${cc:-?}" "${un:-?}" "$batches"
  echo "$SB,${LABEL[$SB]},$(awk "BEGIN{printf \"%.2f\",$reqB/1048576}"),${devB:-0},${ab:-0},${cc:-0},${un:-0},$batches" >> "$CSV"
done
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "DONE -> $CSV"; column -t -s, "$CSV"
