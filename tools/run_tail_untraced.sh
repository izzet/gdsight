#!/usr/bin/env bash
# UNTRACED control for the 22x tail claim: no datacrumbs anywhere in the loop.
set -u
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CUDA=/usr/local/cuda-12.6
BIN=/tmp/gds_interfere_lat
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
OUT=$HOME/projects/gdstrace/results/xlayer/tail_untraced.csv
gcc -O2 -o "$BIN" "$REPO/workloads/gds_interfere_lat.c" \
  -I"$CUDA/include" -L"$CUDA/lib64" -lcufile -lcudart -lcuda -lpthread
printf 'condition,rep,metric,unit,value\n' > "$OUT"
for rep in 1 2 3; do
  for cond in alone large_4MiB_4; do
    if [ "$cond" = alone ]; then NL=0; LC=0; else NL=4; LC=400; fi
    sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
    line=$("$BIN" "$F" 4 "$NL" 3072 4194304 2000 "$LC" 2>/dev/null | grep '^LAT')
    p50=$(sed 's/.*p50_us=\([0-9.]*\).*/\1/' <<<"$line")
    p99=$(sed 's/.*p99_us=\([0-9.]*\).*/\1/' <<<"$line")
    echo "rep$rep $cond -> p50=$p50 p99=$p99"
    printf '%s,%s,small_p50,us,%s\n' "$cond" "$rep" "$p50" >> "$OUT"
    printf '%s,%s,small_p99,us,%s\n' "$cond" "$rep" "$p99" >> "$OUT"
  done
done
echo "DONE -> $OUT"
