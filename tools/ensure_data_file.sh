#!/usr/bin/env bash
# ensure_data_file.sh FILE [SIZE_MIB] — create the shared test file the experiment drivers read, if it is
# missing or smaller than SIZE_MIB (default 2048, as tools/overhead_bench.sh creates it). Written with
# O_DIRECT so the blocks are allocated on the device and FIEMAP reports real extents.
set -euo pipefail
F=$1
MIB=${2:-2048}
need=$((MIB * 1024 * 1024))
have=$(stat -c %s "$F" 2>/dev/null || echo 0)
if [ "$have" -lt "$need" ]; then
  mkdir -p "$(dirname "$F")"
  echo "creating $F ($MIB MiB)" >&2
  dd if=/dev/zero of="$F" bs=1M count="$MIB" oflag=direct status=none
fi
