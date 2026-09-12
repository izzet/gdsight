#!/usr/bin/env bash
# CANDIDATE LIFETIME: does retiring a completed operation from the address candidate set matter?
#
# The address basis maps an NVMe command's sector to the operation whose byte range contains it.
# Without a completion record every operation stays a candidate for the whole run, so a range that is
# read more than once is ambiguous forever. The cufile plugin's 'batchdone' record (per reaped cookie
# from cuFileBatchIOGetStatus) bounds each candidate's lifetime, and tools/batch_addr_attr.py reports
# the attribution both gated and ungated.
#
# Two arms, because one alone proves nothing:
#   headline: the paper's own NIXL batch case (disjoint KV slots). REGRESSION arm - ranges never
#             repeat, so the gate must change nothing. Confirms the fix costs no attribution.
#   control:  overlapping ranges (8 KiB reads on a 4 KiB stride) at batch=1, so each operation is
#             alone in flight and every overlap is purely historical. POSITIVE arm - the gate must
#             resolve all of them. Confirms the fix does something.
set -u
PREFIX=$HOME/dc-prefix
NIXL_PY=${NIXL_PY:-$HOME/nixl-venv/bin/python}
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
SYSCUFILE=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.0
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
OUT=$HOME/projects/gdstrace/results/xlayer/candidate-lifetime.txt
KV=$HOME/projects/gdstrace/workloads/nixl_gds_kv.py

# NOTE: run this from a script file, never by pasting the body into a shell. The datacrumbs stop path
# runs `pkill -f 'sbin/datacrumbs run'`, which matches an interactive shell's own command line.

{
  echo "# Candidate lifetime, gated vs ungated address attribution (tools/run_candidate_lifetime.sh)"
  echo "# host $(hostname), $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT"

arm() { # label args...
  local label=$1; shift
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  local APP="env LD_PRELOAD=$SYSCUFILE LD_LIBRARY_PATH=$CUDALIB $NIXL_PY $KV --file $F $*"
  datacrumbs_run --app "$APP" >/dev/null 2>&1
  sleep 1
  local T; T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  {
    echo
    echo "## $label"
    echo "# workload: $*"
    echo "# trace: $(basename "$T")"
    python3 "$HOME/projects/gdstrace/tools/batch_addr_attr.py" "$T" "$F"
  } >> "$OUT"
}

arm "headline (regression): NIXL KV, disjoint slots" \
  --na 0 --nb 2000 --sb 2560 --stride 65536 --kv-align 3072 --batch 64
arm "control (positive): overlapping ranges, one op in flight" \
  --na 0 --nb 500 --sb 8192 --stride 4096 --kv-align 0 --batch 1

cat "$OUT"
