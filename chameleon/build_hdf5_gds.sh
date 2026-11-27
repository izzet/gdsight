#!/usr/bin/env bash
# build_hdf5_gds.sh — build HDF5 >=1.14 + the GPUDirect-Storage VFD (nv-legate/vfd-gds) so HDF5 apps
# can read datasets directly into GPU memory over cuFile/GDS. Needed for the HDF5+GDS keystone
# (results/xlayer/hdf5-gds.md). apt's HDF5 is 1.10 (too old); vfd-gds requires >=1.14.
#
# Artifacts install OUTSIDE the repo (large binaries): HDF5 -> $H5_PREFIX, VFD -> $VFD_PREFIX.
# Idempotent-ish: skips a phase whose install marker already exists (pass FORCE=1 to rebuild).
#
# VFD_FORK selects the VFD: nv-legate (default; the fork behind the published numbers) or hpc-io
# (upstream hpc-io/vfd-gds, tag 1.0.3, installed to its own prefix for comparison; see
# results/xlayer/hdf5-gds-hpc-io.md).
#
# Usage:  bash chameleon/build_hdf5_gds.sh                  # build both (HDF5 then VFD)
#         FORCE=1 bash chameleon/build_hdf5_gds.sh          # force rebuild
#         VFD_FORK=hpc-io bash chameleon/build_hdf5_gds.sh  # hpc-io/vfd-gds 1.0.3 -> ~/vfd-gds-hpcio
set -euo pipefail

H5_VER=${H5_VER:-1.14.5}
CUDA=${CUDA:-/usr/local/cuda-12.6}
SRC=${SRC:-$HOME/src}
H5_PREFIX=${H5_PREFIX:-$HOME/hdf5-gds}
VFD_FORK=${VFD_FORK:-nv-legate}
HPCIO_TAG=${HPCIO_TAG:-1.0.3}
case "$VFD_FORK" in
  nv-legate)
    VFD_SRC=${VFD_SRC:-$SRC/vfd-gds-nvidia}      # nv-legate fork (production; opens O_DIRECT)
    VFD_PREFIX=${VFD_PREFIX:-$HOME/vfd-gds} ;;
  hpc-io)
    VFD_SRC=${VFD_SRC:-$SRC/vfd-gds-hpcio-$HPCIO_TAG}  # upstream; cuFile reopens O_DIRECT itself
    VFD_PREFIX=${VFD_PREFIX:-$HOME/vfd-gds-hpcio} ;;
  *) echo "VFD_FORK must be nv-legate or hpc-io (got: $VFD_FORK)"; exit 1 ;;
esac
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
if [ "$VFD_FORK" = hpc-io ]; then
  if [ "${FORCE:-0}" = 1 ] || [ ! -f "$VFD_PREFIX/lib/libhdf5_vfd_gds.so" ]; then
    echo "== vfd-gds (hpc-io $HPCIO_TAG) -> $VFD_PREFIX =="
    [ -d "$VFD_SRC" ] || git clone --branch "$HPCIO_TAG" https://github.com/hpc-io/vfd-gds "$VFD_SRC"
    cd "$VFD_SRC"
    rm -rf build && mkdir build && cd build
    cmake .. -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$VFD_PREFIX" \
      -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF \
      -DHDF5_C_COMPILER_EXECUTABLE="$H5_PREFIX/bin/h5cc" \
      -DHDF5_C_LIBRARY_hdf5="$H5_PREFIX/lib/libhdf5.so" \
      -DCMAKE_PREFIX_PATH="$CUDA"
    make -j"$JOBS"
    make install
    echo "  VFD installed: $(ls "$VFD_PREFIX"/lib/libhdf5_vfd_gds.so 2>/dev/null || echo MISSING)"
  else
    echo "== vfd-gds (hpc-io) already built — skip =="
  fi
elif [ "${FORCE:-0}" = 1 ] || [ ! -f "$VFD_PREFIX/lib/libhdf5_vfd_gds.so" ]; then
  echo "== vfd-gds (nv-legate) -> $VFD_PREFIX =="
  [ -d "$VFD_SRC" ] || git clone https://github.com/nv-legate/vfd-gds "$VFD_SRC"
  cd "$VFD_SRC"
  rm -rf build && mkdir build && cd build
  # BUILD_EXAMPLES/BUILD_TESTING OFF: the bundled examples + gds_test don't link the CUDA runtime
  # (undefined cudaMalloc/…) and gds_test uses parallel-HDF5 collective calls absent in serial HDF5.
  # Only the library (libhdf5_vfd_gds.so) is needed.
  cmake .. -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$VFD_PREFIX" \
    -DHDF5_ROOT="$H5_PREFIX" \
    -DCMAKE_PREFIX_PATH="$H5_PREFIX" \
    -DCUDAToolkit_ROOT="$CUDA" \
    -DCMAKE_CUDA_ARCHITECTURES=80 \
    -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF
  make -j"$JOBS"
  make install
  echo "  VFD installed: $(ls "$VFD_PREFIX"/lib/libhdf5_vfd_gds.so 2>/dev/null || echo MISSING)"
else
  echo "== vfd-gds already built — skip =="
fi

echo ""
echo "DONE ($VFD_FORK VFD). To use:"
echo "  export HDF5_PLUGIN_PATH=$VFD_PREFIX/lib"
echo "  export LD_LIBRARY_PATH=$H5_PREFIX/lib:$VFD_PREFIX/lib:\$LD_LIBRARY_PATH"
echo "  H5_PREFIX=$H5_PREFIX  VFD_PREFIX=$VFD_PREFIX"
