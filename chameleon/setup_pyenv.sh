#!/usr/bin/env bash
# setup_pyenv.sh — recreate the GDSight build + Python env (idempotent).
#   C++/CMake toolchain via apt (system); Python GDS readers in an isolated venv.
#   Everything lands on the OS root, so it's captured by `cc-snapshot`.
set -euo pipefail
VENV=${VENV:-/opt/gds-venv}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "== C++/CMake toolchain (apt) =="
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential cmake ninja-build pkg-config git python3-venv python3-dev

echo "== Python venv at $VENV (cc-owned, no sudo for pip) =="
if [ ! -x "$VENV/bin/python" ]; then
  sudo mkdir -p "$VENV" && sudo chown "$(id -u):$(id -g)" "$VENV"
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel setuptools

echo "== install GDS readers (kvikio / cupy / DALI) =="
REQ="$HERE/requirements.lock.txt"; [ -f "$REQ" ] || REQ="$HERE/requirements.txt"
"$VENV/bin/pip" install \
  --extra-index-url https://pypi.nvidia.com \
  --extra-index-url https://developer.download.nvidia.com/compute/redist \
  -r "$REQ"

echo "== verify =="
"$VENV/bin/python" - <<'PY'
import numpy, cupy, kvikio, nvidia.dali as dali
print(f"OK: numpy {numpy.__version__} | cupy {cupy.__version__} | kvikio {kvikio.__version__} | dali {dali.__version__}")
PY
echo "Done. Activate with:  source $VENV/bin/activate"
echo "GDS sanity:  KVIKIO_COMPAT_MODE=OFF python verify_kvikio.py  (must read with is_compat_mode_preferred=False)"
