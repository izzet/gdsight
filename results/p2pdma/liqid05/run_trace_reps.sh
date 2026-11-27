#!/usr/bin/env bash
export SLURM_JOB_ID= CUDA=/usr/local/cuda-12.8 HDF5_PLUGIN_PATH=$HOME/vfd-gds/lib LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:$HOME/hdf5-gds/lib:$HOME/vfd-gds/lib
cd $HOME/projects/gdsight
for r in 1 2 3; do rm -rf /tmp/hdf5_traces; echo "--- rep $r"; bash tools/trace_hdf5_gds.sh 2>&1 | grep -E "cuFileRead="; done
echo DONE
