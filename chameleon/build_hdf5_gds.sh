#!/usr/bin/env bash
# build_hdf5_gds.sh — build HDF5 >=1.14 + the GPUDirect-Storage VFD (hpc-io/vfd-gds) so HDF5 apps
# can read datasets directly into GPU memory over cuFile/GDS. Needed for the HDF5 case
# (results/xlayer/hdf5-gds.md). apt's HDF5 is 1.10 (too old); vfd-gds requires >=1.14.
#
# Artifacts install OUTSIDE the repo (large binaries): HDF5 -> $H5_PREFIX, VFD -> $VFD_PREFIX.
# Idempotent-ish: skips a phase whose install marker already exists (pass FORCE=1 to rebuild).
#
# VFD_FORK selects the VFD:
#   hpc-io     (default) upstream hpc-io/vfd-gds, tag $HPCIO_TAG (1.0.3).
#   nv-legate  the NVIDIA fork used by the previous implementation and the published measurements
#              (archived 2026-10-05). Use it to reproduce the paper's exact numbers.
# Both install to $VFD_PREFIX (default ~/vfd-gds, which the run/trace scripts read). The prefix records
# the fork it holds in .vfd-fork; switching forks on an existing prefix needs FORCE=1.
#
# Usage:  bash chameleon/build_hdf5_gds.sh                     # HDF5, then hpc-io/vfd-gds 1.0.3
#         FORCE=1 bash chameleon/build_hdf5_gds.sh             # force rebuild
#         VFD_FORK=nv-legate bash chameleon/build_hdf5_gds.sh  # the published (nv-legate) VFD
set -euo pipefail

H5_VER=${H5_VER:-1.14.5}
CUDA=${CUDA:-/usr/local/cuda-12.6}
SRC=${SRC:-$HOME/src}
H5_PREFIX=${H5_PREFIX:-$HOME/hdf5-gds}
VFD_FORK=${VFD_FORK:-hpc-io}
HPCIO_TAG=${HPCIO_TAG:-1.0.3}
case "$VFD_FORK" in
  hpc-io)    VFD_SRC=${VFD_SRC:-$SRC/vfd-gds-hpcio-$HPCIO_TAG} ;;  # cuFile reopens O_DIRECT itself
  nv-legate) VFD_SRC=${VFD_SRC:-$SRC/vfd-gds-nvidia} ;;            # opens O_DIRECT; archived
  *) echo "VFD_FORK must be hpc-io or nv-legate (got: $VFD_FORK)"; exit 1 ;;
esac
VFD_PREFIX=${VFD_PREFIX:-$HOME/vfd-gds}
JOBS=${JOBS:-32}
mkdir -p "$SRC"

echo "== deps =="
command -v cmake >/dev/null || { echo "cmake missing"; exit 1; }
[ -f "$CUDA/include/cufile.h" ] || { echo "cufile.h missing under $CUDA"; exit 1; }

# ---------- Phase 1: HDF5 ----------
if [ "${FORCE:-0}" = 1 ] || [ ! -f "$H5_PREFIX/lib/libhdf5.so" ]; then
  echo "== HDF5 $H5_VER -> $H5_PREFIX =="
  cd "$SRC"
  tag="hdf5_${H5_VER}"
  if [ ! -d "hdf5-${tag}" ]; then
    url="https://github.com/HDFGroup/hdf5/archive/refs/tags/${tag}.tar.gz"
    echo "  downloading $url"
    curl -fsSL "$url" -o "hdf5-${tag}.tar.gz"
    tar xzf "hdf5-${tag}.tar.gz"
  fi
  d=$(find "$SRC" -maxdepth 1 -type d -name "hdf5-${tag}*" | head -1)
  cd "$d"
  rm -rf build && mkdir build && cd build
  cmake .. -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$H5_PREFIX" \
    -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_JAVA=OFF \
    -DHDF5_BUILD_TOOLS=ON -DHDF5_BUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF \
    -DHDF5_ENABLE_PARALLEL=OFF -DBUILD_SHARED_LIBS=ON \
    -DHDF5_ENABLE_Z_LIB_SUPPORT=OFF -DHDF5_ENABLE_SZIP_SUPPORT=OFF
  make -j"$JOBS"
  make install
  echo "  HDF5 installed: $("$H5_PREFIX"/bin/h5cc -showconfig 2>/dev/null | grep -i 'HDF5 Version' || echo '(built)')"
else
  echo "== HDF5 already built ($H5_PREFIX/lib/libhdf5.so) — skip (FORCE=1 to rebuild) =="
fi

# ---------- Phase 2: vfd-gds ----------
VFD_LIB="$VFD_PREFIX/lib/libhdf5_vfd_gds.so"
VFD_MARK="$VFD_PREFIX/.vfd-fork"
# A prefix built before VFD_FORK existed has a library but no marker: it holds nv-legate.
have_fork=$( [ -f "$VFD_MARK" ] && cat "$VFD_MARK" || { [ -f "$VFD_LIB" ] && echo nv-legate || echo none; } )

if [ "${FORCE:-0}" != 1 ] && [ -f "$VFD_LIB" ] && [ "$have_fork" = "$VFD_FORK" ]; then
  echo "== vfd-gds ($VFD_FORK) already built in $VFD_PREFIX — skip =="
elif [ "${FORCE:-0}" != 1 ] && [ -f "$VFD_LIB" ]; then
  echo "ERROR: $VFD_PREFIX holds the $have_fork VFD, not $VFD_FORK."
  echo "       Rerun with FORCE=1 to replace it, or set VFD_PREFIX to another directory."
  exit 1
else
  echo "== vfd-gds ($VFD_FORK) -> $VFD_PREFIX =="
  if [ "$VFD_FORK" = hpc-io ]; then
    [ -d "$VFD_SRC" ] || git clone --branch "$HPCIO_TAG" https://github.com/hpc-io/vfd-gds "$VFD_SRC"
  else
    [ -d "$VFD_SRC" ] || git clone https://github.com/nv-legate/vfd-gds "$VFD_SRC"
  fi
  cd "$VFD_SRC"
  rm -rf build && mkdir build && cd build
  # BUILD_EXAMPLES/BUILD_TESTING OFF: the bundled examples + tests don't link the CUDA runtime
  # (undefined cudaMalloc/…) and gds_test uses parallel-HDF5 collective calls absent in serial HDF5.
  # Only the library (libhdf5_vfd_gds.so) is needed.
  if [ "$VFD_FORK" = hpc-io ]; then
    cmake .. -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$VFD_PREFIX" \
      -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF \
      -DHDF5_C_COMPILER_EXECUTABLE="$H5_PREFIX/bin/h5cc" \
      -DHDF5_C_LIBRARY_hdf5="$H5_PREFIX/lib/libhdf5.so" \
      -DCMAKE_PREFIX_PATH="$CUDA"
  else
    cmake .. -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$VFD_PREFIX" \
      -DHDF5_ROOT="$H5_PREFIX" \
      -DCMAKE_PREFIX_PATH="$H5_PREFIX" \
      -DCUDAToolkit_ROOT="$CUDA" \
      -DCMAKE_CUDA_ARCHITECTURES=80 \
      -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF
  fi
  make -j"$JOBS"
  make install
  echo "$VFD_FORK" > "$VFD_MARK"
  echo "  VFD installed: $(ls "$VFD_LIB" 2>/dev/null || echo MISSING)"
fi

echo ""
echo "DONE ($VFD_FORK VFD). To use:"
echo "  export HDF5_PLUGIN_PATH=$VFD_PREFIX/lib"
echo "  export LD_LIBRARY_PATH=$H5_PREFIX/lib:$VFD_PREFIX/lib:\$LD_LIBRARY_PATH"
echo "  H5_PREFIX=$H5_PREFIX  VFD_PREFIX=$VFD_PREFIX"
