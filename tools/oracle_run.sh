#!/usr/bin/env bash
set -u
PREFIX=$HOME/dc-prefix
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib:/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
SIZE=65536; NREADS=4000
cp /tmp/gds_oracle /tmp/gds_oracle_dc; datacrumbs_track --executable /tmp/gds_oracle_dc >/dev/null 2>&1
OUT=/home/cc/projects/gdstrace/results/xlayer/oracle.csv
echo "regime,nvme_scored,corr_id_pct,lba_unique_pct" > "$OUT"
run(){ local label="$1"; shift                       # remaining args = app args after the file
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  datacrumbs_run --app "/tmp/gds_oracle_dc $F $* $SIZE $NREADS ${HOT:-}" >/tmp/orc_$label.out 2>&1
  sleep 1
  local T; T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  grep -iE 'ORACLE' /tmp/orc_$label.out || true
  local line; line=$(python3 ~/projects/gdstrace/tools/oracle_score.py --trace "$T" --file "$F" --slot "$SIZE" --label "$label")
  echo "$line"; echo "$line" | grep '^CSV,' | sed 's/^CSV,//' >> "$OUT"
  echo "----------------------------------------"
}
run "sync_T1"            threads 1
run "concurrent_T16"     threads 16
run "async_IF64"         async   64
HOT=64 run "async_overlap_hot64"  async 64    # 4000 reads over 64 slots -> heavy overlap
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
echo "DONE"; column -t -s, "$OUT"
