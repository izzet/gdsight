# GDS toolkit — `/opt/gds-tools`

GPUDirect Storage bring-up + smoke-test toolkit for the Chameleon A100 snapshot
**`grc-ub2404-nvk-gds-a100-cu126-v2`**. Everything here is captured in the image and is
group-accessible. (Also symlinked at `~/projects/gdstrace/chameleon`.)

## TL;DR — make a relaunched node GDS-ready (per-instance steps)
```bash
/opt/gds-tools/mount_gds_nvme.sh                                  # mount local NVMe -o data=ordered
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect: NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate                                 # kvikio / cupy / DALI
KVIKIO_COMPAT_MODE=OFF python /opt/gds-tools/verify_kvikio.py     # true-GDS read sanity
bash setup_datacrumbs_runtime.sh                                  # ONLY for tracer/experiments: eBPF caps + /var/run/datacrumbs
```
Step-by-step relaunch guide: **`launch-from-snapshot-README.md`**.

## Baked into the image (no setup needed)
- **Open** NVIDIA driver 560.35.05 (Dual MIT/GPL) — *required*; the proprietary driver cannot load nvidia-fs.
- **`linux-nvidia` 6.8.0-1051 kernel** (GDS-patched nvme) + `amd_iommu=off` + nvidia_fs autoload.
- **nvidia-fs 2.28.4** DKMS (with the zstd / `KBUILD_EXTRA_SYMBOLS` source patches).
- **Python env `/opt/gds-venv`**: kvikio-cu12, cupy-cuda12x, nvidia-dali-cuda120.
- **C++/CMake toolchain**: cmake, ninja, gcc/g++, CUDA 12.6 `nvcc`, `libcufile-dev`.

## Per-instance (NOT in the image)
- **GDS:** mount the local NVMe `-o data=ordered` → `mount_gds_nvme.sh` (a different physical disk each launch; the snapshot can't bake a disk mount).
- **Tracer/experiments:** `setup_datacrumbs_runtime.sh` re-applies the eBPF file-caps (the `security.capability`
  xattr is dropped by cc-snapshot's tar) and creates `/var/run/datacrumbs` (tmpfs `/run`, wiped each boot).
  Skipping it makes `datacrumbs_run` fail silently (0-byte traces / 0.000 GiB/s). Idempotent.

## Files
| file | purpose |
|---|---|
| `mount_gds_nvme.sh` | mount local NVMe `data=ordered` (safe; `FORMAT=1` to mkfs a raw disk) |
| `setup_datacrumbs_runtime.sh` | **per-instance tracer runtime**: eBPF caps + `/var/run/datacrumbs` (required before any traced run) |
| `build_datacrumbs.sh` | build the DataCrumbs eBPF tracer + DFAnalyzer on a fresh node (not banked) |
| `setup_nixl_venv.sh` | rebuild `~/nixl-venv` (nixl-cu12 + cupy) for the real-NIXL cross-check (not banked) |
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
- `../docs/CHAMELEON-GDS-BRINGUP-LOG.md` — chronological worklog (every issue + fix).
- `../results/SMOKE-TEST-REPORT-CHAMELEON.md` — gate + smoke results.

The research *workspace* (pre-proposal brief, DeltaAI/Delta reports, `results/`) is intentionally
**NOT** in this image — it lives in the user's home / git. These two docs are copied here so the
image self-documents.
