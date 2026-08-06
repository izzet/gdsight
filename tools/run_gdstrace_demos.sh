#!/usr/bin/env bash
# Reproduce the GDSight demos end-to-end: DataCrumbs (cuFile + NVMe plugins) -> DFTracer trace ->
# DFAnalyzer (per-op cross-layer attribution + amplification).
# Prereqs (see ../docs/DATACRUMBS-GDS-BUILD-LOG.md): DataCrumbs built/installed in ~/dc-prefix, DFAnalyzer in
# ~/dfa-venv (dftracer-utils==0.0.5), kvikio/DALI in /opt/gds-venv, GDS mounted, dataset + npy staged.
# Usage: run_gdstrace_demos.sh [gdsio|kvikio|dali|espn|all]
set -u
PREFIX=$HOME/dc-prefix
CUDA=/usr/local/cuda-12.6
DS=/mnt/nvme1/gdstrace-smoke/dataset.bin
NPY=/mnt/nvme1/gdstrace-smoke/npy
TRACEROOT=/mnt/nvme1/gdstrace-smoke/dc-traces
GDSIO=$CUDA/gds/tools/gdsio
export PATH=$PREFIX/sbin:$PREFIX/bin:$PATH LD_LIBRARY_PATH=$PREFIX/lib
source "$PREFIX/bin/datacrumbs_setup" >/dev/null 2>&1
ulimit -c 0
sudo setcap cap_sys_admin,cap_bpf,cap_perfmon,cap_dac_read_search+ep "$PREFIX/sbin/datacrumbs" 2>/dev/null

latest_trace(){ find "$TRACEROOT" -name '*.pfw.gz' -newer /tmp/.dc_mark 2>/dev/null | head -1; }
run_trace(){  # $1 = app command (already tracked/wrapped as needed)
  touch /tmp/.dc_mark; sleep 1
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  timeout 240 datacrumbs_run --app "$1" 2>&1 | grep -iE 'Throughput|records=|iters=|Bandwidth' | head -2
}
analyze(){  # $1 = trace file
  local d=/tmp/dfa_demo; rm -rf "$d"; mkdir -p "$d"; cp "$1" "$d"/
  ~/dfa-venv/bin/python "$(dirname "$0")/dfa_drive.py" "$d" /tmp/dfa_demo_run 2>/dev/null \
    | sed -n '/per-LAYER/,/^DONE/p'
}

demo_gdsio(){  # device-command amplification (4x at 4MiB)
  echo "### gdsio -i4M GPU_DIRECT (expect ~4x device-cmd amplification) ###"
  [ -x /tmp/gdsio_dc ] || { cp "$GDSIO" /tmp/gdsio_dc; datacrumbs_track --executable /tmp/gdsio_dc; }
  run_trace "/tmp/gdsio_dc -f $DS -d 0 -w 4 -s 256M -i 4M -I 0 -x 0"; analyze "$(latest_trace)"
}
demo_kvikio(){  # sub-16K POSIX path divergence (gds_stats blind)
  echo "### kvikio bimodal (small<16K -> POSIX, large -> GDS); gds_stats reports posix=0 ###"
  run_trace "datacrumbs_wrap /opt/gds-venv/bin/python3.12 $(dirname "$0")/../step3/step3_mixed.py --p-small 0.6 --secs 8"
  analyze "$(latest_trace)"
}
demo_dali(){  # whole-file >MDTS amplification + per-read handle reg
  echo "### DALI numpy GPU reader (expect ~2.5x amplification + per-read handle reg) ###"
  run_trace "datacrumbs_wrap /opt/gds-venv/bin/python3.12 $(dirname "$0")/../step3/step3_dali.py $NPY 8"
  analyze "$(latest_trace)"
}
demo_espn(){  # batch reader: arg-walk + per-read handle-reg pathology
  echo "### ESPN cufile_bread (batch); 4096 nvme -> cuFileBatchIOSubmit; handle-reg overhead ###"
  [ -x /tmp/espn_bread ] || { echo "build /tmp/espn_bread first (see LOG: g++ cufile_bread.cc -lcufile -lcudart -lcuda -lcrypto -lssl)"; return; }
  datacrumbs_track --executable /tmp/espn_bread >/dev/null 2>&1
  run_trace "/tmp/espn_bread $DS 0 128 32 1"; analyze "$(latest_trace)"
}

case "${1:-all}" in
  gdsio) demo_gdsio;; kvikio) demo_kvikio;; dali) demo_dali;; espn) demo_espn;;
  all) demo_gdsio; demo_kvikio; demo_dali; demo_espn;;
  *) echo "usage: $0 [gdsio|kvikio|dali|espn|all]";;
esac
