#!/usr/bin/env bash
# xlayer_sweep.sh — Study A/B sweep: trace gdsio across I/O size x pattern x path,
# then measure per-op cross-layer amplification (tools/xlayer_amp.py) for each cell.
# Produces the amplification-law table: A_cmd(S), A_byte(S), effective device max-xfer.
set -u
PREFIX=$HOME/dc-prefix; CUDA=/usr/local/cuda-12.6
GDSIO=$CUDA/gds/tools/gdsio; TGDSIO=/tmp/gdsio_dc
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
S=${S:-256M}; THREADS=${THREADS:-1}
SIZES=(${SIZES:-4K 16K 64K 256K 1M 4M})
PATTERNS=${PATTERNS:-seq}          # seq|rand (gdsio -I 0|2)
XFER=${XFER:-0}                    # 0 = GDS (-x0), 2 = bounce (-x2) for divergence study
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib"
OUT=/home/cc/projects/gdstrace/results/xlayer; mkdir -p "$OUT"
TAG=${TAG:-x${XFER}}; CSV="$OUT/amp_${TAG}.csv"
echo "label,cufile_ops,A_cmd_mean,A_byte_mean,dev_maxKiB,unattributed" > "$CSV"

[ -f "$F" ] || dd if=/dev/zero of="$F" bs=1M count=2048 oflag=direct status=none
[ -x "$TGDSIO" ] || { cp "$GDSIO" "$TGDSIO"; datacrumbs_track --executable "$TGDSIO" >/dev/null 2>&1; }
reset(){ sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1; sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1; true; }

for pat in $PATTERNS; do
  I=0; [ "$pat" = rand ] && I=2
  for sz in "${SIZES[@]}"; do
    reset; sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
    datacrumbs_run --app "$TGDSIO -f $F -d 0 -w $THREADS -s $S -i $sz -I $I -x $XFER" >/dev/null 2>&1
    sleep 1
    T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
    label="x${XFER}_${pat}_${sz}"
    line=$(python3 ~/projects/gdstrace/tools/xlayer_amp.py "$T" "$label" 2>/dev/null)
    echo "$line"
    echo "$line" | grep '^CSV,' | sed 's/^CSV,//' >> "$CSV"
    echo "------------------------------------------------------------"
  done
done
reset
echo "DONE → $CSV"
column -t -s, "$CSV"
