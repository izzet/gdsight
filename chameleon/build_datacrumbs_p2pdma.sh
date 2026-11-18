#!/usr/bin/env bash
# build_datacrumbs_p2pdma.sh — build a second DataCrumbs install for the kernel PCI P2PDMA transport,
# next to the paper's (~/dc-prefix is left untouched).
#
# Uses host config izzet-gdstrace-node-pcidma from external/datacrumbs (branch feat/p2pdma): the
# cuFile plugin against the CUDA 12.8 libcufile, and the p2pdma plugin (pci_p2pdma_map_segment) in
# place of nvidiafs. Produces ~/dc-prefix-p2pdma, built in external/datacrumbs/build-p2pdma.
# Reuses ~/dfa-venv; pass SKIP_DFA=0 to rebuild it as well.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PREFIX=${PREFIX:-$HOME/dc-prefix-p2pdma}
export HOSTCFG=${HOSTCFG:-izzet-gdstrace-node-pcidma}
export BUILD=${BUILD:-build-p2pdma}
export SKIP_DFA=${SKIP_DFA:-1}
exec bash "$HERE/build_datacrumbs.sh"
