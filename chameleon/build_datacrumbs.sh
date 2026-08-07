#!/usr/bin/env bash
# build_datacrumbs.sh — build the DataCrumbs eBPF tracer + DFAnalyzer on a fresh
# Chameleon v3 GDS node (the snapshot image predates the tracer work). Reproducible
# version of ../docs/DATACRUMBS-GDS-BUILD-LOG.md. Run from the repo root after submodules are
# initialized (git submodule update --init --recursive).
#
# Produces: ~/dc-prefix (libbpf 1.5 + bpftool 7.5 + datacrumbs), ~/dfa-venv (DFAnalyzer).
# Verify after:  /opt/gds-tools/... ; see "smoke" at the end.
set -euo pipefail
PREFIX=$HOME/dc-prefix
SRC=$HOME/src
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DC="$REPO/external/datacrumbs"
DFA="$REPO/external/dfanalyzer"
TRACEDIR=${TRACEDIR:-/mnt/nvme1/gdstrace-smoke/dc-traces}
HOSTCFG=izzet-gdstrace-node        # matches etc/datacrumbs/configs/<host>.yaml + this node's hostname
mkdir -p "$PREFIX" "$SRC" "$TRACEDIR"
say(){ echo; echo "=== $* ==="; }

say "1/6 apt deps + asm header symlink"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential cmake ninja-build git patchelf pkg-config bc \
  clang llvm-dev libclang-dev \
  libopenmpi-dev libyaml-cpp-dev libjson-c-dev libelf-dev zlib1g-dev \
  libssl-dev python3-venv
sudo ln -sfn /usr/include/x86_64-linux-gnu/asm /usr/include/asm   # eBPF compile needs <asm/...>

say "2/6 libbpf 1.5.0 (source → \$PREFIX)"
[ -d "$SRC/libbpf" ] || git clone -q --depth 1 --branch v1.5.0 https://github.com/libbpf/libbpf.git "$SRC/libbpf"
make -s -C "$SRC/libbpf/src" -j"$(nproc)"                                   # build shared first
make -s -C "$SRC/libbpf/src" PREFIX="$PREFIX" LIBDIR="$PREFIX/lib" install install_uapi_headers
ls "$PREFIX"/lib/libbpf.so.1.5.0   # sanity

say "3/6 bpftool 7.5.0 (source → \$PREFIX)"
[ -d "$SRC/bpftool" ] || git clone -q --recurse-submodules --depth 1 --branch v7.5.0 https://github.com/libbpf/bpftool.git "$SRC/bpftool"
make -s -C "$SRC/bpftool/src" -j"$(nproc)"
# bash-completion install dir is root-owned; install just the binary (ignore completion error)
make -s -C "$SRC/bpftool/src" prefix="$PREFIX" install 2>/dev/null || true
"$PREFIX"/sbin/bpftool version | head -1

say "4/6 datacrumbs (cmake/ninja → \$PREFIX)"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
cd "$DC"
rm -rf build
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_PREFIX_PATH="$PREFIX;/usr/lib/llvm-18" \
  -DBPFTOOL_EXECUTABLE="$PREFIX/sbin/bpftool" \
  -DDATACRUMBS_HOST="$HOSTCFG" \
  -DDATACRUMBS_LAUNCHER_TYPE=SLURM \
  -DDATACRUMBS_CONFIGURED_TRACE_DIR="$TRACEDIR" \
  -DDATACRUMBS_TRACE_ALL_PROCESSES_OPT=ON
# TRACE_ALL is MANDATORY and defaults to OFF. With it OFF, need_tracing() requires the app's TGID in
# pid_map, which is only seeded by a uprobe on datacrumbs_start in the client .so, i.e. only for apps
# launched under LD_PRELOAD of libdatacrumbs_client.so. Our workload drivers preload the system
# libcufile instead (and the client lib segfaults the kvikio/cupy python stack), so an OFF build
# collects 0 events for EVERY app, silently and with a successful exit status.
# generation must precede the BPF-object build (explorer→generator emit per-host *.bpf.c)
ninja -C build datacrumbs_explorer datacrumbs_generator
ninja -C build run_explorer
ninja -C build run_generator
ninja -C build
ninja -C build install
test "$(grep -c undefined build/.ninja_log 2>/dev/null || echo 0)" -ge 0   # noop guard

say "5/6 runtime setup (caps + libbpf on system path)"
sudo cp -a "$PREFIX"/lib/libbpf.so* /usr/local/lib/ && sudo ldconfig
sudo setcap cap_sys_admin,cap_bpf,cap_perfmon,cap_dac_read_search+ep "$PREFIX"/sbin/datacrumbs
getcap "$PREFIX"/sbin/datacrumbs

say "6/6 DFAnalyzer venv (~/dfa-venv) — dfanalyzer NON-editable (namespace merge)"
python3 -m venv "$HOME/dfa-venv"
"$HOME/dfa-venv/bin/pip" install -q --upgrade pip wheel
"$HOME/dfa-venv/bin/pip" install -q "dftracer-utils==0.0.5"   # pin: 0.0.9 renamed Reader→TraceReader
"$HOME/dfa-venv/bin/pip" install -q "$DFA"                    # NOT -e (editable shadows dftracer.utils)
"$HOME/dfa-venv/bin/python" -c "import dftracer.analyzer, dftracer.utils; print('DFAnalyzer OK')"

cat <<EOF

DONE. Toolchain in $PREFIX ; analyzer in ~/dfa-venv .
Smoke (overhead): N=3 bash $REPO/tools/overhead_bench.sh
NOTE: trace-write on stop is currently buggy (see ../docs/DATACRUMBS-GDS-BUILD-LOG.md:
  sticky run_id + log-perm trips set -e in stop → 0-byte .pfw.gz). Overhead numbers
  do NOT depend on it (probes attach & fire regardless). dc_reset between runs.
EOF
