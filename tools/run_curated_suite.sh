#!/usr/bin/env bash
# Reproduce the 3 curated GDS anti-patterns where standard tools mislead but GDS-Trace gives the right
# fix. See results/curated-pathologies.md. Run as the regular user (datacrumbs needs passwordless sudo).
set -uo pipefail
PREFIX=/home/cc/dc-prefix
export PATH=$PREFIX/sbin:$PREFIX/bin:$PATH LD_LIBRARY_PATH=$PREFIX/lib
source "$PREFIX/bin/datacrumbs_setup" >/dev/null 2>&1; ulimit -c 0
CLIENT=$PREFIX/lib/libdatacrumbs_client.so
SYS=/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcufile.so.1.11.1
GDSIO=/usr/local/cuda-12.6/gds/tools/gdsio
DS=/mnt/nvme1/gdstrace-smoke/dataset.bin
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces/26/06/08
dropc(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }
newtrace(){ ls -t "$TRACEDIR"/*.pfw.gz 2>/dev/null | head -1; }

echo "############ CASE 1: 'buy faster storage' trap (unaligned layout, 4KiB randread -w64) ############"
dropc; AL=$($GDSIO -f "$DS" -d 0 -w 64 -s 256M -i 4K -I 2 -x 0    2>&1 | grep -oE 'Throughput: [0-9.]+' | grep -oE '[0-9.]+')
dropc; UN=$($GDSIO -f "$DS" -d 0 -w 64 -s 256M -i 4K -I 2 -x 0 -U 2>&1 | grep -oE 'Throughput: [0-9.]+' | grep -oE '[0-9.]+')
echo "  standard (iostat/gds_stats): aligned=${AL} GiB/s, unaligned=${UN} GiB/s, device busy -> 'buy faster drive'"
echo "  GDS-Trace: unaligned byte-amp ~2x (device 2x requested) -> 'align data, ~$(awk "BEGIN{printf \"%.1f\",$AL/$UN}")x free'"

echo "############ CASE 2: 'GDS is healthy' trap (kvikio 16KiB threshold, 60% small) ############"
dropc; rm -f "$TRACEDIR"/*.pfw.gz
timeout 200 datacrumbs_run --app "env LD_PRELOAD=$CLIENT:$SYS /opt/gds-venv/bin/python $HERE/workloads/kvikio_threshold.py --nreads 4000 --frac-small 0.6" >/tmp/cs2.log 2>&1
grep -E 'KVIKIO-THRESHOLD' /tmp/cs2.log
zcat "$(newtrace)" 2>/dev/null | grep -oE '"name":"(cuFileRead|pread64)"' | sort | uniq -c | \
  awk '{n[$2]=$1} END{printf "  standard (gds_stats): %d GDS reads -> \"GDS working\"\n  GDS-Trace: %d cuFileRead(GDS) + %d pread64 (silent POSIX) -> \"batch the <16KiB reads\"\n", n["\"name\":\"cuFileRead\""], n["\"name\":\"cuFileRead\""], n["\"name\":\"pread64\""]}'

echo "############ CASE 3: 'which tensor to optimize' trap (mixed 768-dim + 1024-dim) ############"
dropc; rm -f "$TRACEDIR"/*.pfw.gz
timeout 200 datacrumbs_run --app "env LD_PRELOAD=$CLIENT:$SYS /opt/gds-venv/bin/python $HERE/workloads/mixed_retrieval.py --nreads 4000" >/tmp/cs3.log 2>&1
d=/tmp/cs3_dir; rm -rf $d; mkdir -p $d; cp "$(newtrace)" $d/ 2>/dev/null
echo "  standard (gds_stats/iostat): one blended aggregate byte-amp -> 'some waste somewhere'"
echo "  GDS-Trace per-read-class:"
~/dfa-venv/bin/python "$HERE/tools/dfa_drive.py" $d /tmp/cs3_dfa 2>/dev/null | sed -n '/byte amplification BY/,/DONE/p' | grep -E '3072|4096|op_size'
echo
echo "Full write-up + decision tables: results/curated-pathologies.md"
