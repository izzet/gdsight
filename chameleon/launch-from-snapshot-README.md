# Relaunching the GDS snapshot — `grc-ub2404-nvk-gds-a100-cu126-v2`

This image is **GDS-capable out of the box**: open NVIDIA driver (560.35.05, Dual MIT/GPL),
the **`linux-nvidia` 6.8.0-1051 kernel with the GDS-patched nvme**, nvidia-fs 2.28.4 (+ symvers
patches), `amd_iommu=off`, nvidia_fs autoload, the C++ toolchain, and the Python env at
`/opt/gds-venv` (kvikio/cupy/DALI). **Toolkit scripts live in `/opt/gds-tools`** (also symlinked at
`~/projects/gdstrace/chameleon`). See `CHAMELEON-GDS-BRINGUP-LOG.md` for how it was built.

**The only per-instance step is the local NVMe mount** (`data=ordered`) — the snapshot captures the
OS root, not the physical NVMe, so each new instance prepares its own disk in one command.

---

## 1. Reserve a node (same class)
Lease an **A100 bare-metal node** (the `compute_liqid` / A100 class at CHI@TACC) via the Chameleon
GUI or `openstack reservation`. The image is built for this hardware (AMD EPYC + A100-PCIE).

## 2. Launch from the snapshot
Launch an instance choosing image **`grc-ub2404-nvk-gds-a100-cu126-v2`** (NOT the base
`CC-Ubuntu24.04-CUDA`). Associate a floating IP, then `ssh cc@<ip>`.
> One instance per bare-metal node: to relaunch on the *same* physical node, delete the current
> instance first. To run old + new together, lease a second node.

## 3. Make the local NVMe GDS-ready (one command)
```bash
cd /opt/gds-tools
DEV=/dev/nvme1n1 ./mount_gds_nvme.sh            # mounts an existing ext4 disk -o data=ordered
# brand-new / raw disk? format first (DESTRUCTIVE — asks you to confirm):
DEV=/dev/nvme1n1 FORMAT=1 ./mount_gds_nvme.sh
```
(omit `DEV=` to auto-pick the first non-root NVMe.)

## 4. Verify the gate
```bash
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:|Driver Info'
#   expect:  NVMe : Supported   |   IOMMU: disabled   |   Nvidia Open Driver Installed
bash ./post_reboot_smoke.sh                      # full gate + Step-1 ceiling + gds_stats
```

## 5. Python env (kvikio / DALI)
```bash
source /opt/gds-venv/bin/activate
KVIKIO_COMPAT_MODE=OFF python /opt/gds-tools/verify_kvikio.py
#   expect: is_compat_mode_preferred=False, DATA CORRECT, "TRUE-GDS kvikio read OK"
```

---

## Gotchas (hard-won — see the LOG)
- **Do NOT disable ACS** (it destabilized P2P and dropped an NVMe controller; `amd_iommu=off` alone is enough).
- If `gdscheck` does **not** show `NVMe : Supported`, check in order:
  `uname -r` = `*-nvidia` · `modinfo -F license nvidia` = `Dual MIT/GPL` · `lsmod | grep nvidia_fs` ·
  `grep amd_iommu=off /proc/cmdline` · mount is `data=ordered`.
- **Rebuild from scratch on a non-snapshot node:** `/opt/gds-tools/provision_gds.sh` (driver/kernel/nvidia-fs)
  then `/opt/gds-tools/setup_pyenv.sh` (Python env). The `linux-nvidia` kernel still needs a reboot there.
- GDS **reads** are safe; treat GDS **writes** cautiously (a GDS write first surfaced the controller drop).
