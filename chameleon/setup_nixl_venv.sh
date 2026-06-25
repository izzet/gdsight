#!/usr/bin/env bash
# setup_nixl_venv.sh — reproducible venv for the real end-to-end NIXL GDS keystone.
# Installs the CUDA-12 NIXL wheel (bundles UCX; includes the GDS backend) + cupy as a VRAM
# allocator. No source build, no etcd, no torch needed for the single-agent GDS path.
set -euo pipefail
VENV=${VENV:-$HOME/nixl-venv}
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# IMPORTANT: nixl-cu12 (matches this node's CUDA 12.6 driver). The default `nixl` meta-pkg
# pulls cu13, whose runtime is too new -> cudaSetDevice "driver version insufficient".
"$VENV/bin/pip" install -q nixl-cu12 cupy-cuda12x
"$VENV/bin/python" - <<'PY'
import nixl_cu12, cupy
from nixl_cu12._api import nixl_agent, nixl_agent_config
a = nixl_agent("setupcheck", nixl_agent_config(backends=[]))
assert "GDS" in a.get_plugin_list(), "GDS backend missing"
print("OK: nixl-cu12 + GDS backend + cupy", cupy.__version__, "ready in", "$VENV")
PY
cat <<EOF
NIXL venv ready at $VENV .
Notes:
 - import as 'nixl_cu12' (the cu13 default wheel mismatches the 12.6 driver).
 - cupy is used ONLY to allocate/register VRAM buffers; AVOID cupy compute kernels
   (e.g. .sum()) -- they JIT a cubin our driver rejects (CUDA_ERROR_INVALID_IMAGE).
 - run the keystone:  NIXL_PY=$VENV/bin/python bash tools/run_nixl_e2e.sh
EOF
