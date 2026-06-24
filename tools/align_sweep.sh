#!/usr/bin/env bash
# align_sweep.sh — Study A (alignment axis): trace gds_align_probe across alignment cases,
# measure per-op A_byte (device bytes / requested bytes) to quantify GDS read-amplification
# from sub-4K / misaligned offset / misaligned device-pointer reads.
set -u
PREFIX=$HOME/dc-prefix
PROBE=/tmp/gds_align_probe; PROBE_DC=/tmp/gds_align_probe_dc
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
COUNT=${COUNT:-2000}; STRIDE=${STRIDE:-524288}
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib:/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64"
OUT=/home/cc/projects/gdstrace/results/xlayer; mkdir -p "$OUT"; CSV="$OUT/align.csv"
echo "case,iosize,foff_mis,ptr_mis,cufile_ops,A_cmd_mean,A_byte_mean,dev_maxKiB,unattributed" > "$CSV"

cp "$PROBE" "$PROBE_DC"; datacrumbs_track --executable "$PROBE_DC" >/dev/null 2>&1
reset(){ sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1; sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1; true; }

# label iosize foff_mis ptr_mis
cases=(
 "aligned_64K   65536 0   0"
 "off512_64K    65536 512 0"
 "off100_64K    65536 100 0"
 "aligned_4K    4096  0   0"
 "sub4k_512     512   0   0"
 "sub4k_512_off 512   512 0"
 "size_4095     4095  0   0"
 "ptr512_64K    65536 0   512"
)
for c in "${cases[@]}"; do
  set -- $c; label=$1 io=$2 fo=$3 pm=$4
  reset; sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  datacrumbs_run --app "$PROBE_DC $F $io $fo $pm $COUNT $STRIDE" >/dev/null 2>&1
  sleep 1
  T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  line=$(python3 ~/projects/gdstrace/tools/xlayer_amp.py "$T" "$label" 2>/dev/null)
  echo "$line"
  csv=$(echo "$line" | grep '^CSV,' | sed 's/^CSV,//')   # label,ops,A_cmd,A_byte,maxKiB,unattr
  IFS=, read -r _l ops acmd abyte mk un <<<"$csv"
  echo "$label,$io,$fo,$pm,$ops,$acmd,$abyte,$mk,$un" >> "$CSV"
  echo "------------------------------------------------------------"
done
reset
echo "DONE → $CSV"; echo; column -t -s, "$CSV"
