#!/usr/bin/env bash
# run_write_keystone.sh -- the GDS write keystone (results/xlayer/write-keystone.md).
#
# Traces misaligned cuFileWrite workloads and reports the cross-layer picture the built-in tools cannot
# produce: app writes vs GDS engagement vs POSIX fallback vs per-direction device commands, with each
# device command attributed to its causing cuFileWrite.
#
# Requires the direction-aware block probe (req->cmd_flags & REQ_OP_MASK). Two operational gotchas that
# cost real time, both documented in ../docs/DATACRUMBS-GDS-BUILD-LOG.md:
#   - datacrumbs_run is SIGKILLed during cleanup and takes its process group with it, so each traced run
#     is launched from its own throwaway script and scored afterwards, never in the same pipeline.
#   - find -newermt is unreliable on this node (clock skew); locate traces with ls -t instead.
set -uo pipefail
PREFIX=${DC_PREFIX:-$HOME/dc-prefix}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
F=${1:-/mnt/nvme1/gdstrace-smoke/wtest.bin}
TRACEROOT=${TRACEROOT:-/mnt/nvme1/gdstrace-smoke/dc-traces}
GDSIO=${GDSIO:-/usr/local/cuda-12.6/gds/tools/gdsio}
export PATH=$PREFIX/sbin:$PREFIX/bin:$PATH LD_LIBRARY_PATH=$PREFIX/lib

[ -x /tmp/gdsio_dc ] || cp "$GDSIO" /tmp/gdsio_dc
[ -f "$F" ] || { dd if=/dev/zero of="$F" bs=1M count=512 oflag=direct status=none; sync; }

traced(){  # $1 = label, $2 = gdsio args
  local runner; runner=$(mktemp)
  cat > "$runner" <<EOF
PREFIX=$PREFIX
export PATH=\$PREFIX/sbin:\$PREFIX/bin:\$PATH LD_LIBRARY_PATH=\$PREFIX/lib
source "\$PREFIX/bin/datacrumbs_setup" >/dev/null 2>&1
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
setsid timeout 300 datacrumbs_run --app "/tmp/gdsio_dc -f $F -d 0 -w 1 $2" \
  > /tmp/wk_run.log 2>&1 < /dev/null
EOF
  bash "$runner" >/dev/null 2>&1; rm -f "$runner"
  echo; echo "### $1"
  grep -E "IoType" /tmp/wk_run.log | tail -1
  python3 "$REPO/tools/write_attr.py" "$(ls -t "$TRACEROOT"/*/*/*/*.pfw.gz | head -1)"
}

traced "Result 1 - 4 KiB unaligned (silent GDS bypass)"        "-s 64M -i 4K  -I 3 -x 0 -U"
traced "Result 2 - 16 KiB unaligned (gds_stats reports healthy)" "-s 64M -i 16K -I 3 -x 0 -U"
traced "Control  - 1 MiB aligned (true GDS write path)"        "-s 64M -i 1M  -I 3 -x 0"
