# gdstrace

Research workspace for **GDSight** — a per-operation, cross-layer tracer correlating **cuFile**
(userspace) ↔ **nvidia-fs** (kernel) ↔ **NVMe**, to attribute GPUDirect Storage (GDS) pathologies
(silent POSIX fallback, I/O amplification, GPU stall) to the causing application operation. Extends
DFTracer / DFAnalyzer.

## Repo map
| path | what |
|---|---|
| `gds-trace-brief.md` | the pre-proposal (idea, novelty, go/no-go gate, **Appendix A** smoke tests) |
| `chamREADME.md` | Chameleon true-GDS bring-up plan |
| `CHAMELEON-GDS-BRINGUP-LOG.md` | **full worklog** of getting true GDS working on Chameleon — every issue + fix |
| `SMOKE-TEST-REPORT-CHAMELEON.md` | Chameleon results — **TRUE GDS achieved** |
| `SMOKE-TEST-REPORT.md` / `SMOKE-TEST-REPORT-DELTA-A100.md` | earlier DeltaAI / Delta runs (compat-only dead ends) |
| `chameleon/` | the GDS bring-up + smoke **toolkit** (also deployed to `/opt/gds-tools` in the snapshot image) |
| `results/chameleon/` | raw `gdscheck` / `gdsio` / `gds_stats` outputs |

## TL;DR — true GDS on Chameleon
A reusable, **grc-project-private** Chameleon image **`grc-ub2404-nvk-gds-a100-cu126-v3`** boots
GDS-ready: open NVIDIA driver 560.35.05 + **`linux-nvidia` 6.8.0-1051 kernel (GDS-patched nvme)** +
nvidia-fs 2.28.4 + `amd_iommu=off`. The only per-instance step is mounting the local NVMe
`-o data=ordered`:

```bash
git clone https://github.com/izzet/gdstrace.git ~/projects/gdstrace   # this workspace
/opt/gds-tools/mount_gds_nvme.sh                                       # mount local NVMe data=ordered
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect: NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate                                      # kvikio / cupy / DALI
```
Full relaunch guide: `chameleon/launch-from-snapshot-README.md`. Hard-won gotchas (e.g. **do NOT
disable ACS**) are in `chameleon/README.md` and the LOG.

## Status
True GDS working + validated on a fresh instance; image banked (v2). **Next:** Appendix-A Steps 3–4
— drive kvikio/DALI over ragged/compressed chunks to catch a *silent* per-op fallback, and show the
coarse tools are blind to it (the project's motivating Figure 1).
