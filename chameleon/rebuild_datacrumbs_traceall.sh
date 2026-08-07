#!/usr/bin/env bash
# Reconfigure + rebuild + install DataCrumbs in TRACE-ALL mode, in place.
# Use after editing a plugin .bpf.c, instead of the full build_datacrumbs.sh.
# TRACE_ALL_PROCESSES_OPT defaults to OFF and a plain rebuild silently drops it -> 0 events for
# every app. This script always re-asserts it.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/dc-prefix}"
DC="${DC:-$HOME/projects/gdstrace/external/datacrumbs}"
HOSTCFG="${HOSTCFG:-izzet-gdstrace-node}"
TRACEDIR="${TRACEDIR:-/mnt/nvme1/gdstrace-smoke/dc-traces}"

mkdir -p "$TRACEDIR"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
cd "$DC"

cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_PREFIX_PATH="$PREFIX;/usr/lib/llvm-18" \
  -DBPFTOOL_EXECUTABLE="$PREFIX/sbin/bpftool" \
  -DDATACRUMBS_HOST="$HOSTCFG" \
  -DDATACRUMBS_LAUNCHER_TYPE=SLURM \
  -DDATACRUMBS_CONFIGURED_TRACE_DIR="$TRACEDIR" \
  -DDATACRUMBS_TRACE_ALL_PROCESSES_OPT=ON

grep -q '^#define DATACRUMBS_TRACE_ALL_PROCESSES 1' build/include/datacrumbs/datacrumbs_config.h \
  || { echo "FATAL: configure did not set TRACE_ALL=1"; exit 1; }

# the generators emit the per-host .bpf.c, so they must run before the BPF object build
ninja -C build datacrumbs_explorer datacrumbs_generator
ninja -C build run_explorer
ninja -C build run_generator
# regenerates the EMBEDDED skeleton (editing only the .bpf.o does nothing)
ninja -k 0 -C build
# install without ninja (the default target races a clean step that deletes datacrumbs.bpf.o)
cmake -P build/cmake_install.cmake
cp build/data/categories-cc-"$HOSTCFG".json build/data/probes-cc-"$HOSTCFG".json \
  "$PREFIX"/etc/datacrumbs/data/
sudo setcap cap_sys_admin,cap_bpf,cap_perfmon,cap_dac_read_search+ep "$PREFIX"/sbin/datacrumbs
getcap "$PREFIX"/sbin/datacrumbs
echo "REBUILD OK (trace-all)"
