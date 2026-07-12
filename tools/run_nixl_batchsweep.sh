#!/usr/bin/env bash
# Mechanism depth: vary NIXL's GDS batch size and show that BOTH failure modes of the standard
# observers are batch-driven -- the cuFile-API granularity coarsens (fewer batch ops for the same
# device reads) and corr_id collapses harder (more device cmds fire async after each submit returns),
# while our per-NVMe-cmd LBA per-class amplification is invariant. Fixed KV size = 2048 B (Llama-3.1-70B
# fp8: 2 (K,V) x 8 KV heads x 128 elem). Table V (KV-only) is: NA=0 SB=2048 bash run_nixl_batchsweep.sh
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-test-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
NA=${NA:-200}; SA=1048576; NB=2000; SB=${SB:-2048}
OUT=/home/cc/projects/gdstrace/results/xlayer; CSV="$OUT/nixl_batchsweep.csv"
echo "batch,batch_ops,device_reads,api_coarsening,B_A_byte,corr_id_correct_pct,corr_id_unattr_pct" > "$CSV"
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
for B in 1 8 32 64 128; do
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na $NA --sa $SA --nb $NB --sb $SB --batch $B"
  datacrumbs_run --app "$APP" >/tmp/bsw_$B.out 2>&1
  sleep 1
  T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  line=$(python3 $HOME/projects/gdstrace/tools/keystone_gds_score.py "$T" "$F" $((NA*SA)) $((NB*SB)) 2>/dev/null)
  ab=$(grep -oE 'B=[0-9.]+x' <<<"$line" | tail -1 | tr -dc '0-9.')
  cc=$(grep -oE '[0-9]+% correct' <<<"$line" | tr -dc '0-9')
  un=$(grep -oE '[0-9]+% unattributed' <<<"$line" | tr -dc '0-9')
  bops=$(zcat "$T" 2>/dev/null | grep -oc cuFileBatchIOSubmit)
  dev=$(zcat "$T" 2>/dev/null | grep -oc nvme_setup_cmd)
  coarse=$(awk "BEGIN{if($bops>0)printf \"%.0f\",$dev/$bops; else print \"NA\"}")
  printf "batch=%-4s  cuFileBatchIOSubmit=%-4s  device_reads=%-5s  API_coarsening=%sx/op  B_A_byte=%-6s  corr_id %s%%correct/%s%%unattr\n" \
    "$B" "$bops" "$dev" "$coarse" "${ab:-?}" "${cc:-?}" "${un:-?}"
  echo "$B,$bops,$dev,$coarse,${ab:-0},${cc:-0},${un:-0}" >> "$CSV"
done
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "DONE -> $CSV"; column -t -s, "$CSV"
