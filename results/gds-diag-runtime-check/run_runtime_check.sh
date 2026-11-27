#!/usr/bin/env bash
# Validate gds-diag runtime-check on the paper stack (nvidia-fs route): six workloads, with and without --kernel-trace.
cd ~/repro/gds-diag
export HDF5_PLUGIN_PATH=$HOME/vfd-gds/lib LD_LIBRARY_PATH=/usr/local/cuda-12.6/lib64:$HOME/hdf5-gds/lib:$HOME/vfd-gds/lib
S=/mnt/nvme1/gdstrace-smoke; G=/usr/local/cuda-12.6/gds/tools/gdsio
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled >/dev/null
declare -a NAMES=(hdf5_cache_on hdf5_cache_off hdf5_contiguous gdsio_gpu_direct gdsio_cpu_only)
declare -a CMDS=("/tmp/hdf5_gds read $S/h5_chunk.h5 on" "/tmp/hdf5_gds read $S/h5_chunk.h5 off" "/tmp/hdf5_gds read $S/h5_contig.h5 on" "$G -f $S/ovh.dat -d 0 -w 1 -s 1G -i 1M -x 0 -I 0" "$G -f $S/ovh.dat -d 0 -w 1 -s 1G -i 1M -x 1 -I 0")
for i in "${!NAMES[@]}"; do
  for mode in plain ktrace; do
    sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
    if [ $mode = plain ]; then
      python3 gds-diag.py runtime-check --json --path $S -- ${CMDS[$i]} > ~/repro/rc_${NAMES[$i]}_$mode.json 2>/dev/null
    else
      sudo -E python3 gds-diag.py runtime-check --json --kernel-trace --path $S -- ${CMDS[$i]} > ~/repro/rc_${NAMES[$i]}_$mode.json 2>/dev/null
    fi
  done
done
echo DONE
