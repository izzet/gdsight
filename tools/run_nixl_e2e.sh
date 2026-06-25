#!/usr/bin/env bash
# End-to-end REAL NIXL GDS keystone: trace the actual nixl_agent + GDS backend (unmodified
# python, via DATACRUMBS trace-all -- no injection) reading file->GPU with NIXL's heterogeneous
# KV access pattern, then attribute per-class cross-layer. Reproducible: deps in
# chameleon/setup_nixl_venv.sh; workload in workloads/nixl_gds_kv.py.
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-venv/bin/python}   # created by chameleon/setup_nixl_venv.sh
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
NA=${NA:-200}; SA=${SA:-1048576}; NB=${NB:-2000}; SB=${SB:-2560}; BATCH=${BATCH:-64}
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
APP="env LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $HOME/projects/gdstrace/workloads/nixl_gds_kv.py --file $F --na $NA --sa $SA --nb $NB --sb $SB --batch $BATCH"
echo "=== traced run (real nixl_agent + GDS backend, trace-all) ==="
datacrumbs_run --app "$APP" 2>&1 | grep -iE 'NIXL_GDS_KV|Permission|ERR' | head
sleep 2
T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
echo "trace: $(basename "$T")  $(stat -c%s "$T")B"
echo "--- cuFile-layer events NIXL's GDS backend emitted ---"
for ev in cuFileBatchIOSubmit cuFileRead cuFileReadAsync cuFileBatchIOGetStatus; do
  echo "  $ev : $(zcat "$T" 2>/dev/null | grep -oc "\"$ev\"")"
done
echo "  nvfs_io : $(zcat "$T" 2>/dev/null | grep -oc nvfs_io)   p2p_dma_map : $(zcat "$T" 2>/dev/null | grep -oc nvfs_get_p2p_dma_mapping)   nvme_setup_cmd : $(zcat "$T" 2>/dev/null | grep -oc nvme_setup_cmd)"
echo "$T" > /tmp/nixl_last_trace.txt
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
