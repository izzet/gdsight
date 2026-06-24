#!/usr/bin/env bash
# compat-fallback divergence: same aligned reads, true GDS vs forced compat bounce.
# Measure what the cross-layer tracer sees differ: nvfs_io layer, A_byte, attribution.
set -u
PREFIX=$HOME/dc-prefix
export PATH="$PREFIX/sbin:$PREFIX/bin:$PATH" LD_LIBRARY_PATH="$PREFIX/lib:/usr/local/cuda-12.6/targets/x86_64-linux/lib:/usr/local/cuda-12.6/lib64"
F=/mnt/nvme1/gdstrace-smoke/ovh.dat
TRACEDIR=/mnt/nvme1/gdstrace-smoke/dc-traces
# aligned 64K reads, 512K stride, 2000 ops
python3 -c "
f=open('/tmp/aligned.csv','w')
for i in range(2000): f.write(f'{i*524288},65536\n')
f.close()"
cp /tmp/gds_replay /tmp/gds_replay_dc; datacrumbs_track --executable /tmp/gds_replay_dc >/dev/null 2>&1
run(){ # $1=label $2=env-prefix
  sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
  sudo rm -f /var/run/datacrumbs/* /tmp/datacrumbs_*.log >/dev/null 2>&1 || true
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  datacrumbs_run --app "$2 /tmp/gds_replay_dc $F /tmp/aligned.csv 2000" >/tmp/cd_$1.out 2>&1
  sleep 1
  T=$(ls -t "$TRACEDIR"/*/*/*/*.pfw.gz 2>/dev/null | head -1)
  echo "==== $1 ===="; grep -iE 'REPLAY' /tmp/cd_$1.out || true
  python3 ~/projects/gdstrace/tools/xlayer_amp.py "$T" "$1"
}
run "A_true_GDS"  ""
run "B_compat"    "env CUFILE_FORCE_COMPAT_MODE=true"
sudo pkill -9 -f 'sbin/datacrumbs run' >/dev/null 2>&1 || true
