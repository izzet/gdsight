#!/usr/bin/env bash
# trace_hdf5_gds_p2pdma.sh — the HDF5 chunk-cache A/B of trace_hdf5_gds.sh, on the kernel PCI P2PDMA
# transport (CUDA >= 12.8, GDS >= 1.13, use_pci_p2pdma). On this transport nvidia-fs is bypassed, so
# nvfs_io reads 0 even for true GDS; the P2P marker is pci_p2pdma_map_segment instead. See
# results/p2pdma/README.md.
#
# Prereqs: chameleon/enable_p2pdma.sh (then reboot), chameleon/build_datacrumbs_p2pdma.sh, and the
# HDF5 workload built for CUDA 12.8: CUDA=/usr/local/cuda-12.8 bash tools/run_hdf5_gds.sh 64 256
# Usage:   bash tools/trace_hdf5_gds_p2pdma.sh
set -uo pipefail
PREFIX=${PREFIX:-$HOME/dc-prefix-p2pdma}
H5=${H5_PREFIX:-$HOME/hdf5-gds}; VFD=${VFD_PREFIX:-$HOME/vfd-gds}; CUDA=${CUDA:-/usr/local/cuda-12.8}
SCRATCH=${SCRATCH:-/mnt/nvme1/gdstrace-smoke}
TRACEROOT=$SCRATCH/dc-traces
BIN=${BIN:-/tmp/hdf5_gds}
PY=${PY:-$HOME/dfa-venv/bin/python}
OUT=${OUT:-/tmp/hdf5_traces_p2pdma}; mkdir -p "$OUT"

[ -x "$PREFIX/sbin/datacrumbs" ] || { echo "no tracer at $PREFIX (run chameleon/build_datacrumbs_p2pdma.sh)"; exit 1; }
[ -x "$BIN" ] || { echo "no workload at $BIN (run: CUDA=$CUDA bash tools/run_hdf5_gds.sh 64 256)"; exit 1; }
# Capture first: with pipefail, `gdscheck | grep -q` fails when grep exits early and gdscheck gets SIGPIPE.
GDSCHECK_OUT=$("$CUDA/gds/tools/gdscheck" -p 2>/dev/null || true)
if ! grep -q 'NVMe P2PDMA *: Supported' <<<"$GDSCHECK_OUT"; then
  echo "gdscheck does not report 'NVMe P2PDMA : Supported' (run chameleon/enable_p2pdma.sh and reboot)"; exit 1
fi

# Runtime setup the tracer needs on every boot:
#   - datacrumbs_setup dereferences ${!DATACRUMBS_JOB_ID_VAR} (SLURM_JOB_ID) under set -u;
#   - /var/run/datacrumbs is on tmpfs and must exist and be writable.
export SLURM_JOB_ID=${SLURM_JOB_ID:-}
sudo mkdir -p /var/run/datacrumbs && sudo chown "$(id -un):$(id -gn)" /var/run/datacrumbs
# The CUDA 12.8 libcufile must win over the 12.6 RUNPATH baked into the VFD.
export HDF5_PLUGIN_PATH=$VFD/lib
export PATH=$PREFIX/sbin:$PREFIX/bin:$PATH
export LD_LIBRARY_PATH=$CUDA/lib64:$PREFIX/lib:$H5/lib:$VFD/lib
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
pd=c.get('pci_p2pdma_map_segment',0)
nvme=c.get('nvme_setup_cmd',0); px=c.get('pread64',0)+c.get('read',0)
verdict='TRUE GDS (p2pdma)' if pd>0 else 'SILENT COMPAT (0 P2P)'
print(f"cuFileRead={cf}  p2pdma_map={pd}  nvme_setup_cmd={nvme}  posix(pread/read)={px}  -> {verdict}")
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

echo "== trace HDF5 chunk-cache A/B on the PCI P2PDMA transport =="
trace_case "$SCRATCH/h5_chunk.h5"  on  "chunked-cacheON"
trace_case "$SCRATCH/h5_chunk.h5"  off "chunked-cacheOFF"
trace_case "$SCRATCH/h5_contig.h5" on  "contiguous"
echo "  traces saved under $OUT/"
