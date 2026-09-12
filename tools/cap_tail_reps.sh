#!/usr/bin/env bash
# D4: is raising max_sectors_kb tail-neutral? Re-measures the claim "p99 4080 (cap 1280) vs 4058
# (cap 2048)", which came from results/xlayer/interference.md (2026-06-25), a single run whose
# parameters were never recorded.
#
# This version fixes the contention level to the SAME condition the paper's 22x tail claim uses
# (4 large threads x 4 MiB, i.e. tail_reps.csv's large_4MiB_4) so the cap result and the headline
# tail number live in one documented regime, and runs REPS repetitions per cap.
#
# Must run from a script file, not inline: the pkill pattern matches any command line containing it.
set -u
export PATH=$HOME/dc-prefix/sbin:$HOME/dc-prefix/bin:$PATH LD_LIBRARY_PATH=$HOME/dc-prefix/lib
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CUDA=/usr/local/cuda-12.6
BIN=/tmp/gds_interfere
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
TD=/mnt/nvme1/gdstrace-smoke/dc-traces
SELF=$HOME/projects/gdstrace
DEV=nvme1n1
REPS=${REPS:-3}
OUT=$SELF/results/xlayer/cap_tail_reps.csv

gcc -O2 -o "$BIN" "$REPO/workloads/gds_interfere.c" \
  -I"$CUDA/include" -L"$CUDA/lib64" -lcufile -lcudart -lcuda -lpthread

ORIG=$(cat /sys/block/$DEV/queue/max_sectors_kb)
restore() { echo "$ORIG" | sudo tee /sys/block/$DEV/queue/max_sectors_kb >/dev/null; echo "restored cap=$ORIG"; }
trap restore EXIT

printf 'cap_kb,rep,metric,unit,value\n' > "$OUT"

for cap in 1280 2048; do
  echo "$cap" | sudo tee /sys/block/$DEV/queue/max_sectors_kb >/dev/null
  actual=$(cat /sys/block/$DEV/queue/max_sectors_kb)
  [ "$actual" = "$cap" ] || { echo "FATAL: cap did not take (asked $cap, got $actual)"; exit 1; }
  echo "== cap=${cap}KiB (verified) =="
  for r in $(seq 1 "$REPS"); do
    sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
    sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
    sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
    # args: file  n_small_threads  n_large  small_sz  large_sz  small_reads  large_reads
    datacrumbs_run --app "$BIN $F 4 4 3072 4194304 2000 400" >/dev/null 2>&1
    sleep 1
    T=$(ls -t "$TD"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
    [ -n "$T" ] || { echo "  rep$r: NO TRACE"; continue; }
    line=$(python3 "$SELF/tools/interfere_score.py" --trace "$T" --small 3072 2>/dev/null | grep '^CSV,.*,small,')
    p50=$(cut -d, -f6 <<<"$line"); p99=$(cut -d, -f7 <<<"$line")
    echo "  rep$r: p50=${p50:-?} p99=${p99:-?}"
    [ -n "${p50:-}" ] && printf '%s,%s,small_p50,us,%s\n' "$cap" "$r" "$p50" >> "$OUT"
    [ -n "${p99:-}" ] && printf '%s,%s,small_p99,us,%s\n' "$cap" "$r" "$p99" >> "$OUT"
  done
done

echo
echo "per-rep rows -> $OUT"
awk -F, 'NR>1{k=$1"_"$3; s[k]+=$5; q[k]+=$5*$5; n[k]++}
  END{for(k in s){m=s[k]/n[k]; sd=(n[k]>1)?sqrt((q[k]-n[k]*m*m)/(n[k]-1)):0;
      printf "cap %-18s %9.1f +/- %6.1f  (n=%d)\n", k, m, sd, n[k]}}' "$OUT" | sort
