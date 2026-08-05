#!/usr/bin/env bash
# D5 + D10: re-run the NIXL keystone cross-check with REPETITIONS, at the KV size the paper declares.
#
# Two problems this fixes:
#   D5  results/xlayer/nixl-e2e-complete.md claims "3-run mean: 12%+/-2 correct, 87%+/-2 unattributed"
#       but only ONE crosscheck artifact is committed (nixl_crosscheck.txt: 9% correct, 90% unattr).
#   D10 Sec V-D declares 2048 B KV reads, but the committed keystone ran at SB=2560, so the
#       "35 calls / 2208 device reads / 87%" numbers come from a size the section does not claim.
#
# Runs the committed driver tools/run_nixl_crosscheck.sh REPS times at SB=2048, preserving each run's
# output instead of letting the driver overwrite nixl_crosscheck.txt.
#
# Must run from a script file, not inline: the driver's pkill pattern matches any command line
# containing it, so inlining makes pkill kill the invoking shell.
set -u
SELF=$HOME/projects/gdstrace
OUT=$SELF/results/xlayer
REPS=${REPS:-3}
SB=${SB:-2048}
export NIXL_PY=$HOME/nixl-venv/bin/python

for r in $(seq 1 "$REPS"); do
  echo "===== rep $r (SB=$SB) ====="
  SB=$SB bash "$SELF/tools/run_nixl_crosscheck.sh" >/tmp/nixl_cc_rep$r.log 2>&1
  cp "$OUT/nixl_crosscheck.txt" "$OUT/nixl_crosscheck_sb${SB}_rep${r}.txt" 2>/dev/null \
    || echo "  rep $r: NO OUTPUT"
  grep -E "corr_id on B|AGGREGATE|PER-CLASS|BatchIOSubmit|nvfs_io=" "$OUT/nixl_crosscheck_sb${SB}_rep${r}.txt" 2>/dev/null \
    | sed 's/^/  /'
done

echo
echo "=== per-rep corr_id on class B ==="
for r in $(seq 1 "$REPS"); do
  grep -H "corr_id on B" "$OUT/nixl_crosscheck_sb${SB}_rep${r}.txt" 2>/dev/null
done
