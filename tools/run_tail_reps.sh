#!/usr/bin/env bash
# Reproduce Fig. 8: traced small-read tail latency under increasing large-read contention.
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CUDA=/usr/local/cuda-12.6
PREFIX=${DC_PREFIX:-$HOME/dc-prefix}
BIN=/tmp/gds_interfere
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
OUT="$REPO/results/xlayer/tail_reps.csv"
REPS=${REPS:-3}

export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
gcc -O2 -o "$BIN" "$REPO/workloads/gds_interfere.c" \
  -I"$CUDA/include" -L"$CUDA/lib64" -lcufile -lcudart -lcuda -lpthread
[ -f "$F" ] || { dd if=/dev/zero of="$F" bs=1M count=2048 oflag=direct status=none; sync; }
printf 'condition,rep,metric,unit,value\n' > "$OUT"

run_condition() { # condition large_threads large_size
  local condition=$1 large_threads=$2 large_size=$3
  for rep in $(seq 1 "$REPS"); do
    sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
    sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
    sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
    datacrumbs_run --app "$BIN $F 4 $large_threads 3072 $large_size 2000 400" >/dev/null 2>&1
    sleep 1
    local trace line p50 p99
    trace=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
    line=$(python3 "$REPO/tools/interfere_score.py" --trace "$trace" --small 3072 | grep '^CSV,.*,small,')
    p50=$(cut -d, -f6 <<<"$line"); p99=$(cut -d, -f7 <<<"$line")
    printf '%s,%s,small_p50,us,%s\n' "$condition" "$rep" "$p50" >> "$OUT"
    printf '%s,%s,small_p99,us,%s\n' "$condition" "$rep" "$p99" >> "$OUT"
  done
}

run_condition alone          0 1048576
run_condition large_1MiB_2  2 1048576
run_condition large_4MiB_2  2 4194304
run_condition large_4MiB_4  4 4194304
echo "DONE -> $OUT"
