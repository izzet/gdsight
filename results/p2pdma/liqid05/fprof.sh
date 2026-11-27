#!/usr/bin/env bash
# fprof.sh — ftrace function-profile kernel calls during each workload, to find a kernel signal that
# separates true GDS (P2P) from host-staged reads on the PCI P2PDMA transport.
# Usage: bash fprof.sh <reps> > out.csv
set -uo pipefail
REPS=${1:-1}
T=/sys/kernel/tracing
export CUDA=/usr/local/cuda-12.8
export HDF5_PLUGIN_PATH=$HOME/vfd-gds/lib
export LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:$HOME/hdf5-gds/lib:$HOME/vfd-gds/lib
G=/usr/local/cuda-12.8/gds/tools
S=/mnt/nvme1/gdstrace-smoke
BIN=/tmp/hdf5_gds

FUNCS="pci_p2pdma_map_segment pci_p2pdma_unmap_mappings p2pdma_page_free pci_p2pdma_distance_many
dma_pci_p2pdma_supported nvme_pci_supports_pci_p2pdma pci_alloc_p2pmem pci_p2pmem_alloc_sgl
nvme_setup_cmd nvme_map_data dma_direct_map_sg iomap_dio_rw filemap_read ext4_file_read_iter"

drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }

setup(){
  echo 0 | sudo tee $T/function_profile_enabled >/dev/null
  : | sudo tee $T/set_ftrace_filter >/dev/null
  for f in $FUNCS; do echo "$f" | sudo tee -a $T/set_ftrace_filter >/dev/null 2>&1 || true; done
}

profile(){ # $1 label, rest = command
  local label=$1; shift
  drop
  echo 0 | sudo tee $T/function_profile_enabled >/dev/null
  echo 1 | sudo tee $T/function_profile_enabled >/dev/null   # re-enable resets counters
  "$@" >/dev/null 2>&1
  echo 0 | sudo tee $T/function_profile_enabled >/dev/null
  sudo sh -c "cat $T/trace_stat/function*" | awk -v L="$label" -v R="$rep" '
    $1!="Function" && $1!~/^-/ && NF>=2 && $2~/^[0-9]+$/ {c[$1]+=$2}
    END {for (f in c) printf "%s,%s,%s,%d\n", L, R, f, c[f]}'
}

setup
echo "case,rep,function,calls"
for rep in $(seq 1 "$REPS"); do
  profile idle                sleep 2
  profile hdf5_cache_on       "$BIN" read "$S/h5_chunk.h5" on
  profile hdf5_cache_off      "$BIN" read "$S/h5_chunk.h5" off
  profile hdf5_contiguous     "$BIN" read "$S/h5_contig.h5" on
  profile gdsio_gpu_direct_1g "$G/gdsio" -f "$S/test.dat" -d 0 -w 1 -s 1G -i 1M -x 0 -I 0
  profile gdsio_cpu_only_1g   "$G/gdsio" -f "$S/test.dat" -d 0 -w 1 -s 1G -i 1M -x 1 -I 0
done
: | sudo tee $T/set_ftrace_filter >/dev/null
