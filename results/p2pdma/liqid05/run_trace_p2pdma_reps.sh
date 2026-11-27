#!/usr/bin/env bash
cd $HOME/projects/gdsight
for r in 1 2 3; do rm -rf /tmp/hdf5_traces_p2pdma; echo "--- rep $r"; bash tools/trace_hdf5_gds_p2pdma.sh 2>&1 | grep -E "cuFileRead=|NO TRACE|does not|no tracer|no workload"; done
echo DONE
