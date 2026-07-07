#!/usr/bin/env bash
# trace_hdf5_gds.sh — trace HDF5 GDS reads through DataCrumbs (cuFile + nvidia-fs + NVMe probes) and
# emit the per-op cross-layer counts, for the chunk-cache silent-GDS A/B. Avoids the datacrumbs_run
# "inline-shell-kill" (never pipe it through head/grep) by redirecting to a log and scoring the trace
# file afterward.
set -uo pipefail
PREFIX=$HOME/dc-prefix
H5=${H5_PREFIX:-$HOME/hdf5-gds}; VFD=${VFD_PREFIX:-$HOME/vfd-gds}; CUDA=${CUDA:-/usr/local/cuda-12.6}
SCRATCH=${SCRATCH:-/mnt/nvme1/gdstrace-smoke}
TRACEROOT=$SCRATCH/dc-traces
BIN=${BIN:-/tmp/hdf5_gds}
PY=${PY:-$HOME/dfa-venv/bin/python}
OUT=${OUT:-/tmp/hdf5_traces}; mkdir -p "$OUT"

export PATH=$PREFIX/sbin:$PREFIX/bin:$PATH
export LD_LIBRARY_PATH=$PREFIX/lib:$H5/lib:$VFD/lib:$CUDA/lib64
source "$PREFIX/bin/datacrumbs_setup" >/dev/null 2>&1
ulimit -c 0
sudo setcap cap_sys_admin,cap_bpf,cap_perfmon,cap_dac_read_search+ep "$PREFIX/sbin/datacrumbs" 2>/dev/null
datacrumbs_track --executable "$BIN" >/dev/null 2>&1

count_layers(){ # $1 = trace file  -> prints the cross-layer counts
  "$PY" - "$1" <<'PY'
import sys, gzip, json, collections
c=collections.Counter()
with gzip.open(sys.argv[1],'rt',errors='replace') as f:
    for ln in f:
        ln=ln.strip().rstrip(',')
        if not ln or ln[0]!='{': continue
        try: e=json.loads(ln)
        except: continue
        c[e.get('name','?')]+=1
cf=c.get('cuFileRead',0)+c.get('cuFileReadAsync',0)+c.get('cuFileBatchIOSubmit',0)
nv=c.get('nvfs_io',0); p2p=c.get('nvfs_get_p2p_dma_mapping',0)
nvme=c.get('nvme_setup_cmd',0); px=c.get('pread64',0)+c.get('read',0)
verdict='TRUE GDS' if nv>0 else 'SILENT COMPAT (0 P2P)'
print(f"cuFileRead={cf}  nvfs_io={nv}  p2p_map={p2p}  nvme_setup_cmd={nvme}  posix(pread/read)={px}  -> {verdict}")
PY
}

trace_case(){ # $1=file $2=cache $3=label
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  touch /tmp/.h5mark; sleep 1
  # setsid: run datacrumbs_run in its own session so its internal cleanup-kill (pkill by pgid) can't
  # cascade to this script and abort the loop after case 1.
  setsid timeout 180 datacrumbs_run --app "$BIN read $1 $2" > "$OUT/run_$3.log" 2>&1 </dev/null || true
  local t; t=$(find "$TRACEROOT" -name '*.pfw.gz' -newer /tmp/.h5mark 2>/dev/null | head -1)
  if [ -z "$t" ]; then echo "  [$3] NO TRACE (see $OUT/run_$3.log)"; return; fi
  cp "$t" "$OUT/trace_$3.pfw.gz"
  printf "  %-24s | %s\n" "$3" "$(count_layers "$OUT/trace_$3.pfw.gz")"
}

echo "== trace HDF5 chunk-cache A/B =="
trace_case "$SCRATCH/h5_chunk.h5"  on  "chunked-cacheON"
trace_case "$SCRATCH/h5_chunk.h5"  off "chunked-cacheOFF"
trace_case "$SCRATCH/h5_contig.h5" on  "contiguous"
echo "  traces saved under $OUT/"
