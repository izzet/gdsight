#!/usr/bin/env bash
# run_hdf5_gds.sh — reproduce the HDF5 chunk-cache silent-GDS finding end-to-end.
# Builds the workload (workloads/hdf5_gds.c) against HDF5 + vfd-gds (chameleon/build_hdf5_gds.sh),
# creates chunked + contiguous datasets host-side, then reads each into GPU memory over the GDS VFD
# and reports whether true GDS engaged (nvidia-fs Reads delta) + throughput.
#
# Signal: /proc/driver/nvidia-fs/stats "Reads : n=.. readMiB=.." — REQUIRES IO stats enabled
# (rw_stats_enabled=1); the always-present "Ops: Read=" line is a dead legacy counter (stays 0).
#
# Prereqs: mount_gds_nvme.sh done; HDF5+vfd-gds built (chameleon/build_hdf5_gds.sh).
# Usage: bash tools/run_hdf5_gds.sh [size_MiB] [chunk_KiB]
set -uo pipefail
H5=${H5_PREFIX:-$HOME/hdf5-gds}; VFD=${VFD_PREFIX:-$HOME/vfd-gds}; CUDA=${CUDA:-/usr/local/cuda-12.6}
SCRATCH=${SCRATCH:-/mnt/nvme1/gdstrace-smoke}
SIZE=${1:-64}; CHUNK=${2:-256}
BIN=${BIN:-/tmp/hdf5_gds}
SELF=$(cd "$(dirname "$0")/.." && pwd)

echo "== enable nvidia-fs IO stats (per-boot) =="
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
cat /sys/module/nvidia_fs/parameters/rw_stats_enabled

echo "== compile workload =="
gcc "$SELF/workloads/hdf5_gds.c" -o "$BIN" -O2 \
  -I"$H5/include" -I"$VFD/include" -I"$CUDA/include" \
  -L"$H5/lib" -lhdf5 -L"$VFD/lib" -lhdf5_vfd_gds -L"$CUDA/lib64" -lcudart -lcuda \
  -Wl,-rpath,"$H5/lib" -Wl,-rpath,"$VFD/lib" -Wl,-rpath,"$CUDA/lib64" 2>&1 | grep -v 'CBSIZE_DEF\|redefined\|note:\|previous def\|^ *[0-9]* |\|^ *|' || true
[ -x "$BIN" ] || { echo "compile failed"; exit 1; }

echo "== create datasets host-side (default VFD, no GDS write) =="
"$BIN" create "$SCRATCH/h5_chunk.h5"  chunk  "$SIZE" "$CHUNK"
"$BIN" create "$SCRATCH/h5_contig.h5" contig "$SIZE" 0

nvfs_n(){   grep -m1 '^Reads' /proc/driver/nvidia-fs/stats | grep -oE 'n=[0-9]+' | cut -d= -f2; }
nvfs_mib(){ grep -m1 '^Reads' /proc/driver/nvidia-fs/stats | grep -oE 'readMiB=[0-9]+' | cut -d= -f2; }
drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }
ab(){ drop; bn=$(nvfs_n); bm=$(nvfs_mib); out=$("$BIN" read "$1" "$2"); an=$(nvfs_n); am=$(nvfs_mib)
      printf "  %-26s | GDS reads +%-5s (+%s MiB) | %s\n" "$3" "$((an-bn))" "$((am-bm))" "$(echo "$out"|grep -oE 'BW=[0-9.]+ GiB/s')"; }

echo "== chunk-cache A/B (chunk=${CHUNK}KiB, size=${SIZE}MiB) =="
ab "$SCRATCH/h5_chunk.h5"  on  "chunked, cache ON (default)"
ab "$SCRATCH/h5_chunk.h5"  off "chunked, cache OFF (fix)"
ab "$SCRATCH/h5_contig.h5" on  "contiguous"
echo "  (cache ON -> +0 = silent compat/CPU-bounce; cache OFF & contiguous -> +N = true GDS/P2P)"
