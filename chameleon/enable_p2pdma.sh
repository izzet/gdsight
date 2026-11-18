#!/usr/bin/env bash
# enable_p2pdma.sh — switch GDS on this node from the nvidia-fs transport to the kernel PCI P2PDMA
# transport, or back with --revert. Follows the NVIDIA GDS troubleshooting guide, section 14
# (NVMe P2PDMA). A reboot is required either way; this script does not reboot.
#
# Requires CUDA >= 12.8 / GDS >= 1.13 with an R570+ open driver, kernel >= 6.2 with CONFIG_PCI_P2PDMA,
# and an Ampere or newer GPU. Setting properties.use_pci_p2pdma alone is not enough; this applies:
#   1. /etc/cufile.json: properties.use_pci_p2pdma and block.nvme.use_pci_p2pdma = true
#   2. /etc/modprobe.d/nvidia-p2pdma.conf: NVreg_RegistryDwords RMForceStaticBar1=1;RmForceDisableIomapWC=1
#      plus ForceP2P=0 on pre-Hopper GPUs (A100, A40, L4, L40)
#   3. /etc/modprobe.d/nvme.conf: nvme_core multipath=N
#   4. update-initramfs for the running kernel
# The original cufile.json is saved once as /etc/cufile.json.pre-p2pdma; --revert restores it.
#
# Usage: bash chameleon/enable_p2pdma.sh [--revert]    then: sudo reboot
set -euo pipefail
CFG=/etc/cufile.json
BAK=/etc/cufile.json.pre-p2pdma
NV=/etc/modprobe.d/nvidia-p2pdma.conf
NVME=/etc/modprobe.d/nvme.conf

if [ "${1:-}" = "--revert" ]; then
  [ -f "$BAK" ] && sudo cp -a "$BAK" "$CFG" && echo "restored $CFG from $BAK"
  sudo rm -f "$NV" "$NVME" && echo "removed $NV $NVME"
  sudo update-initramfs -u -k "$(uname -r)"
  echo "Reverted. Reboot, then check: gdscheck -p shows 'NVMe P2PDMA : Unsupported'."
  exit 0
fi

[ -f "$BAK" ] || sudo cp -a "$CFG" "$BAK"

# 1. cufile.json (JSON with // comments): flip the properties key, add block.nvme if absent.
sudo python3 - "$CFG" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
s, n = re.subn(r'("use_pci_p2pdma"\s*:\s*)false', r'\1true', s, count=1)
if '"block"' not in s:
    s, m = re.subn(r'(\n(\s*)"fs"\s*:\s*\{)',
                   r'\n\2"block": {\n\2    "nvme": {\n\2        "use_pci_p2pdma": true\n\2    }\n\2},\1', s, count=1)
    if m != 1: sys.exit('could not find the "fs" section to insert "block" before')
open(p, 'w').write(s)
import json
d = json.loads(re.sub(r'^\s*//.*$', '', s, flags=re.M))
assert d['properties']['use_pci_p2pdma'] is True and d['block']['nvme']['use_pci_p2pdma'] is True
print('cufile.json: properties.use_pci_p2pdma = block.nvme.use_pci_p2pdma = true')
PY

# 2. driver registry keys (ForceP2P=0 only for pre-Hopper GPUs)
CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)
if awk -v c="$CC" 'BEGIN{exit !(c < 9.0)}'; then
  KEYS="RMForceStaticBar1=1;ForceP2P=0;RmForceDisableIomapWC=1;"
else
  KEYS="RMForceStaticBar1=1;RmForceDisableIomapWC=1;"
fi
echo "options nvidia NVreg_RegistryDwords=\"$KEYS\"" | sudo tee "$NV"

# 3. NVMe multipath off (not supported with upstream P2PDMA)
echo "options nvme_core multipath=N" | sudo tee "$NVME"

# 4. initramfs
sudo update-initramfs -u -k "$(uname -r)"
cat <<EOF
Done. Reboot, then check:
  grep RegistryDwords: /proc/driver/nvidia/params      # the keys above
  cat /sys/module/nvme_core/parameters/multipath       # N
  /usr/local/cuda-12.8/gds/tools/gdscheck -p | grep 'NVMe P2PDMA'   # Supported
EOF
