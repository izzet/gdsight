#!/usr/bin/env bash
# run_reps.sh -- repetitions with variance for the RATE-based claims.
#
# Why this exists: an audit on 2026-07-29 found that exactly one results CSV (amp_perf.csv) recorded
# any variance. Everything else was single-shot, one row per condition. That is why the same quantity
# appeared with four or five different values across our records -- those were separate single runs from
# different days, and nothing recorded that they were.
#
# What needs reps and what does not:
#   - Amplification (A_byte) and command counts are deterministic geometry. Re-measured 2026-07-29 they
#     reproduce the analytic prediction exactly (4.000 / 2.000 / 1.008). No error bars needed.
#   - Throughput and latency are rates and DO vary. The scatter arm of the optimization experiment
#     spans +/-22% run to run, which is what made the 13.7x headline irreproducible.
#
# Writes tidy per-rep rows so any figure or table can be recomputed, and prints mean/sd per arm.
# Usage: bash tools/run_reps.sh [reps]   (default 5).   Env: SB (KV size, default 2048)
set -uo pipefail

REPS=${1:-5}
SB=${SB:-2048}
SELF=$(cd "$(dirname "$0")/.." && pwd)
OUT=$SELF/results/xlayer
CSV=$OUT/reps.csv

NIXL_PY=${NIXL_PY:-$HOME/nixl-test-venv/bin/python}
CUDA=${CUDA:-/usr/local/cuda-12.6}
CUDALIB=$CUDA/targets/x86_64-linux/lib:$CUDA/lib64
SYSCUFILE=$CUDA/targets/x86_64-linux/lib/libcufile.so.0
H5=${H5_PREFIX:-$HOME/hdf5-gds}; VFD=${VFD_PREFIX:-$HOME/vfd-gds}
SCRATCH=${SCRATCH:-/mnt/nvme1/gdstrace-smoke}
F=$SCRATCH/ovh.dat
KVW=$SELF/workloads/nixl_gds_kv.py
H5BIN=/tmp/hdf5_gds_reps

echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }

printf 'experiment,arm,rep,metric,unit,value\n' > "$CSV"
emit(){ printf '%s,%s,%s,%s,%s,%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >> "$CSV"; }

# mean and sample sd from stdin (one value per line)
stats(){ awk '{n++; s+=$1; q+=$1*$1} END{if(n<2){printf "%.4f  n=%d", s/n, n; exit}
              m=s/n; printf "%.4f +/- %.4f  n=%d", m, sqrt((q-n*m*m)/(n-1)), n}'; }

# ---------------------------------------------------------------- optimization arms (the 13.7x headline)
# Untraced: this is a workload-throughput claim, so the tracer should not be in the loop. Traced numbers
# inflate the ratio because the scatter arm is op-rate-bound and pays the most tracing overhead.
opt_run(){ # layout -> useful GiB/s
  drop
  env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB "$NIXL_PY" "$KVW" --file "$F" \
      --na 1 --sa 1048576 --nb 120000 --sb "$SB" --stride 8192 --batch 64 --layout "$1" 2>&1 \
    | grep -oE 'useful_GiBps=[0-9.]+' | tr -dc '0-9.'
}

echo "== optimization arms, SB=${SB}B, untraced, ${REPS} reps =="
for L in scatter aligned packed; do
  vals=$(for r in $(seq 1 "$REPS"); do v=$(opt_run "$L"); emit optimization "$L" "$r" useful_throughput GiBps "$v"; echo "$v"; done)
  printf '  %-8s %s GiB/s\n' "$L" "$(stats <<<"$vals")"
done

# ---------------------------------------------------------------- HDF5 chunk-cache bandwidth (the 2x claim)
gcc "$SELF/workloads/hdf5_gds.c" -o "$H5BIN" -O2 \
  -I"$H5/include" -I"$VFD/include" -I"$CUDA/include" \
  -L"$H5/lib" -lhdf5 -L"$VFD/lib" -lhdf5_vfd_gds -L"$CUDA/lib64" -lcudart -lcuda \
  -Wl,-rpath,"$H5/lib" -Wl,-rpath,"$VFD/lib" -Wl,-rpath,"$CUDA/lib64" 2>/dev/null
if [ -x "$H5BIN" ] && [ -f "$SCRATCH/h5_chunk.h5" ]; then
  h5_run(){ drop; "$H5BIN" read "$1" "$2" 2>/dev/null | grep -oE 'BW=[0-9.]+' | tr -dc '0-9.'; }
  echo "== HDF5 chunk-cache bandwidth, ${REPS} reps =="
  for spec in "chunked_cacheON:$SCRATCH/h5_chunk.h5:on" \
              "chunked_cacheOFF:$SCRATCH/h5_chunk.h5:off" \
              "contiguous:$SCRATCH/h5_contig.h5:on"; do
    IFS=: read -r arm file mode <<<"$spec"
    vals=$(for r in $(seq 1 "$REPS"); do v=$(h5_run "$file" "$mode"); emit hdf5 "$arm" "$r" read_bandwidth GiBps "$v"; echo "$v"; done)
    printf '  %-18s %s GiB/s\n' "$arm" "$(stats <<<"$vals")"
  done
else
  echo "== HDF5 SKIPPED (no binary or no h5_chunk.h5) =="
fi

echo
echo "per-rep rows -> $CSV"
