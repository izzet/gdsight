# GDS toolkit — `/opt/gds-tools`

GPUDirect Storage bring-up + smoke-test toolkit for the Chameleon A100 snapshot
**`grc-ub2404-nvk-gds-a100-cu126-v2`**. Everything here is captured in the image and is
group-accessible. (Also symlinked at `~/projects/gdstrace/chameleon`.)

## TL;DR — make a relaunched node GDS-ready (one per-instance step)
```bash
/opt/gds-tools/mount_gds_nvme.sh                                  # mount local NVMe -o data=ordered
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect: NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate                                 # kvikio / cupy / DALI
KVIKIO_COMPAT_MODE=OFF python /opt/gds-tools/verify_kvikio.py     # true-GDS read sanity
```
Step-by-step relaunch guide: **`launch-from-snapshot-README.md`**.

## Baked into the image (no setup needed)
- **Open** NVIDIA driver 560.35.05 (Dual MIT/GPL) — *required*; the proprietary driver cannot load nvidia-fs.
- **`linux-nvidia` 6.8.0-1051 kernel** (GDS-patched nvme) + `amd_iommu=off` + nvidia_fs autoload.
- **nvidia-fs 2.28.4** DKMS (with the zstd / `KBUILD_EXTRA_SYMBOLS` source patches).
- **Python env `/opt/gds-venv`**: kvikio-cu12, cupy-cuda12x, nvidia-dali-cuda120.
- **C++/CMake toolchain**: cmake, ninja, gcc/g++, CUDA 12.6 `nvcc`, `libcufile-dev`.

## Per-instance (NOT in the image — the local NVMe is a different physical disk each launch)
- Mount the local NVMe `-o data=ordered` → `mount_gds_nvme.sh` (the snapshot can't bake a disk mount).

## Files
| file | purpose |
|---|---|
| `mount_gds_nvme.sh` | mount local NVMe `data=ordered` (safe; `FORMAT=1` to mkfs a raw disk) |
| `post_reboot_smoke.sh` | full gate + Step-1 ceiling + Step-2 + `gds_stats` |
| `verify_kvikio.py` | kvikio true-GDS read sanity (`KVIKIO_COMPAT_MODE=OFF`) |
| `setup_pyenv.sh` | rebuild `/opt/gds-venv` from `requirements*.txt` |
| `provision_gds.sh` | full from-scratch bring-up on a **non-snapshot** node (driver swap, kernel, nvidia-fs) |
| `requirements.txt` / `requirements.lock.txt` | pinned Python deps |
| `launch-from-snapshot-README.md` | step-by-step relaunch guide |

## Gotchas (hard-won)
- **Do NOT disable ACS** — it destabilized P2P and dropped the NVMe controller here; `amd_iommu=off` is enough.
- ext4 must be mounted **`-o data=ordered`** explicitly (cuFile verifies it); `mount_gds_nvme.sh` does this.
- GDS **reads** are safe; GDS **writes** are riskier (first surfaced the controller drop) — create test files with `dd`.

## Full build record / results (in this dir)
- `CHAMELEON-GDS-BRINGUP-LOG.md` — chronological worklog (every issue + fix).
- `SMOKE-TEST-REPORT-CHAMELEON.md` — gate + smoke results.

The research *workspace* (pre-proposal brief, DeltaAI/Delta reports, `results/`) is intentionally
**NOT** in this image — it lives in the user's home / git. These two docs are copied here so the
image self-documents.
