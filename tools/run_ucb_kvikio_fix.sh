#!/usr/bin/env bash
# UC-B closed loop: real kvikio RAG-mix, BASELINE (small reads silently bypass GDS -> POSIX, amplify)
# vs the fix GDS-Trace prescribes (coalesce class-B above the 16 KiB threshold -> takes the GDS P2P
# path AND aligns away the amplification). Traced, analyzed with mixed_score.py.
set -u
PREFIX=$HOME/dc-prefix
KVIKIO_PY=/opt/gds-venv/bin/python
CUDALIB=/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat; TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
bash "$HOME/projects/gdstrace/chameleon/setup_datacrumbs_runtime.sh" >/dev/null 2>&1 || true
"$KVIKIO_PY" -c "import kvikio,cupy" 2>/dev/null && echo "kvikio venv OK" || { echo "kvikio import failed"; exit 1; }

run_arm() { # label coalesce
  local label=$1 coal=$2
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  local APP="env LD_LIBRARY_PATH=$CUDALIB KVIKIO_GDS_THRESHOLD=16384 $KVIKIO_PY $HOME/projects/gdstrace/workloads/rag_mixed.py --path $F --threads 8 --na 200 --sa 1048576 --nb 2000 --sb 3072 --coalesce $coal"
  echo "============================================================"
  echo "### $label  (coalesce=$coal)"
  datacrumbs_run --app "$APP" 2>&1 | grep -iE 'RAG_MIXED|Error|Traceback' | head
  sleep 1; local T; T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  python3 "$HOME/projects/gdstrace/tools/mixed_score.py" --trace "$T" --file "$F" --sb 3072 2>/dev/null
}
run_arm "BASELINE (default kvikio)" 0
run_arm "FIX: coalesce B to 64 KiB" 65536
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
