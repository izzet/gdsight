#!/usr/bin/env bash
# h5_transport_counts.sh PHASE — HDF5 64 MiB / 256 KiB chunks, both VFD forks, three cases, 3 reps.
# Per run: cold page cache; nvidia-fs Reads delta, device sectors read, kernel function hits (ftrace
# profiler), bandwidth. Separate strace runs count pread64 (strace -f -c -e trace=pread64).
set -uo pipefail
PHASE=${1:?phase label}
REPO=${REPO:-$HOME/repro/gdsight-main}
CUDA=${CUDA:-/usr/local/cuda-12.8}
H5=$HOME/hdf5-gds
D=/mnt/nvme1/gdstrace-smoke/cu128
OUT=$HOME/p2pdma07/$PHASE
REPS=${REPS:-3}
mkdir -p "$D" "$OUT"
DEV=nvme1n1
T=/sys/kernel/tracing
FNS="pci_p2pdma_map_segment nvfs_get_p2p_dma_mapping filemap_read iomap_dio_rw"

echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
build(){ # fork prefix bin
  gcc "$REPO/workloads/hdf5_gds.c" -o "$3" -O2 -I"$H5/include" -I"$2/include" -I"$CUDA/include" \
    -L"$H5/lib" -lhdf5 -L"$2/lib" -lhdf5_vfd_gds -L"$CUDA/lib64" -lcudart -lcuda \
    -Wl,-rpath,"$H5/lib" -Wl,-rpath,"$2/lib" -Wl,-rpath,"$CUDA/lib64" 2>/dev/null
  [ -x "$3" ] || { echo "build failed for $1"; exit 1; }
}
build hpc-io    "$HOME/vfd-gds-hpcio-cu128"    /tmp/h5_hpcio
build nv-legate "$HOME/vfd-gds-nvlegate-cu128" /tmp/h5_nvlegate
[ -f "$D/chunk.h5" ]  || /tmp/h5_hpcio create "$D/chunk.h5"  chunk  64 256 >/dev/null
[ -f "$D/contig.h5" ] || /tmp/h5_hpcio create "$D/contig.h5" contig 64 0   >/dev/null

nvfs_n(){ grep -m1 '^Reads' /proc/driver/nvidia-fs/stats | grep -oE 'n=[0-9]+' | cut -d= -f2; }
sect(){ awk '{print $3}' /sys/class/block/$DEV/stat; }
drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }
avail=""; for f in $FNS; do sudo grep -qx "$f" $T/available_filter_functions 2>/dev/null || \
  sudo grep -q "^$f " $T/available_filter_functions && avail="$avail $f"; done
prof_on(){ echo 0 | sudo tee $T/function_profile_enabled >/dev/null
  echo "$avail" | sudo tee $T/set_ftrace_filter >/dev/null
  echo 1 | sudo tee $T/function_profile_enabled >/dev/null; }
prof_off(){ echo 0 | sudo tee $T/function_profile_enabled >/dev/null
  r=""; for f in $avail; do n=$(sudo sh -c "cat $T/trace_stat/function*" | awk -v f=$f '$1==f{s+=$2}END{print s+0}'); r="$r $f=$n"; done
  echo | sudo tee $T/set_ftrace_filter >/dev/null; echo "$r"; }

{
echo "# phase=$PHASE $(date -u) kernel=$(uname -r) driver=$(head -1 /proc/driver/nvidia/version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
$CUDA/gds/tools/gdscheck -p 2>&1 | grep -E 'GDS release|NVMe P2PDMA|NVMe  '
echo "# traced functions:$avail"
echo "fork,case,rep,nvfs_reads,dev_MiB,BW_GiBs,kernel_hits"
} | tee "$OUT/counters.csv.hdr"
: > "$OUT/counters.csv"; : > "$OUT/pread64.csv"
for fork in hpc-io nv-legate; do
  bin=/tmp/h5_${fork/-/}; [ "$fork" = nv-legate ] && bin=/tmp/h5_nvlegate
  for c in "chunk.h5 on cache_on" "chunk.h5 off cache_off" "contig.h5 on contiguous"; do
    set -- $c
    for r in $(seq 1 $REPS); do
      drop; n0=$(nvfs_n); s0=$(sect); prof_on
      bw=$("$bin" read "$D/$1" "$2" 2>&1 | grep -oE 'BW=[0-9.]+' | cut -d= -f2)
      kh=$(prof_off); n1=$(nvfs_n); s1=$(sect)
      echo "$fork,$3,$r,$((n1-n0)),$(( (s1-s0)*512/1048576 )),$bw,$kh" | tee -a "$OUT/counters.csv"
      drop
      p=$(strace -f -c -e trace=pread64 "$bin" read "$D/$1" "$2" 2>&1 >/dev/null | awk '$NF=="pread64"{print $4}')
      echo "$fork,$3,$r,${p:-0}" | tee -a "$OUT/pread64.csv"
    done
  done
done
echo DONE
