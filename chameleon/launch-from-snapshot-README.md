# Relaunching the GDS snapshot — `grc-ub2404-nvk-gds-a100-cu126-v2`

This image is **GDS-capable out of the box**: open NVIDIA driver (560.35.05, Dual MIT/GPL),
the **`linux-nvidia` 6.8.0-1051 kernel with the GDS-patched nvme**, nvidia-fs 2.28.4 (+ symvers
patches), `amd_iommu=off`, nvidia_fs autoload, the C++ toolchain, and the Python env at
`/opt/gds-venv` (kvikio/cupy/DALI). **Toolkit scripts live in `/opt/gds-tools`** (also symlinked at
`~/projects/gdstrace/chameleon`). See `../docs/CHAMELEON-GDS-BRINGUP-LOG.md` for how it was built.

**Per-instance steps** (the snapshot captures the OS root, not the physical NVMe nor tmpfs/xattr state):
1. the **local NVMe mount** (`data=ordered`) — §3 below;
2. for tracing/experiments, the **DataCrumbs runtime setup** (eBPF caps + `/var/run/datacrumbs`) — §5b below.

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

## 5b. DataCrumbs tracer runtime (required before any traced run / experiment)
The snapshot's tar drops two pieces of tracer runtime state, so re-apply them once per instance:
```bash
bash ~/projects/gdstrace/chameleon/setup_datacrumbs_runtime.sh
#   re-applies: (1) eBPF file-caps on ~/dc-prefix/sbin/datacrumbs (security.capability xattr, not
#   preserved by cc-snapshot), (2) /var/run/datacrumbs (tmpfs /run, wiped each boot). Idempotent.
```
**Without this, `datacrumbs_setup` fails silently under `set -e` and `datacrumbs_run` produces 0-byte
traces / 0.000 GiB/s** (probes never attach). Verify with one traced gdsio read:
```bash
N=1 PATTERNS=seq bash ~/projects/gdstrace/tools/overhead_bench.sh   # traced col should be non-zero
```
> On a **reused physical node**, old `/mnt/nvme1/gdstrace-smoke/dc-traces/YY/MM` date-dirs can be
> root-owned from a prior boot (the tracer ran as root) → today's mkdir is denied. Fix once:
> `sudo chown -R cc:cc /mnt/nvme1/gdstrace-smoke/dc-traces`.

---

## Gotchas (hard-won — see the LOG)
- **Do NOT disable ACS** (it destabilized P2P and dropped an NVMe controller; `amd_iommu=off` alone is enough).
- If `gdscheck` does **not** show `NVMe : Supported`, check in order:
  `uname -r` = `*-nvidia` · `modinfo -F license nvidia` = `Dual MIT/GPL` · `lsmod | grep nvidia_fs` ·
  `grep amd_iommu=off /proc/cmdline` · mount is `data=ordered`.
- **Rebuild from scratch on a non-snapshot node:** `/opt/gds-tools/provision_gds.sh` (driver/kernel/nvidia-fs)
  then `/opt/gds-tools/setup_pyenv.sh` (Python env). The `linux-nvidia` kernel still needs a reboot there.
- GDS **reads** are safe; treat GDS **writes** cautiously (a GDS write first surfaced the controller drop).
