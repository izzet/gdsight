# CLAUDE.md — GDS-Trace project context

Orientation for a Claude Code session working in this repo. (Replaces the per-machine `~/.claude`
memory, which is deliberately kept out of the snapshot image for security.)

## What this is
**GDS-Trace** — a per-operation, cross-layer tracer correlating cuFile (user) ↔ nvidia-fs (kernel)
↔ NVMe, to attribute GPUDirect Storage pathologies (silent POSIX fallback, I/O amplification, GPU
stall) to the causing app op. Extends DFTracer/DFAnalyzer. Full pitch: `gds-trace-brief.md`.

## Current state (2026-06-07)
- **TRUE GDS is working** on a Chameleon bare-metal A100. Reusable grc-private image:
  **`grc-ub2404-nvk-gds-a100-cu126-v3`** (or latest `-vN`).
- Toolkit: this repo's `chameleon/` is the source; it's also deployed to **`/opt/gds-tools`** on the
  image. Python env: **`/opt/gds-venv`** (kvikio/cupy/DALI).
- Full build history + every fix: **`CHAMELEON-GDS-BRINGUP-LOG.md`**. Results: `results/chameleon/`.
- **NEXT: Appendix-A Steps 3–4** — drive kvikio/DALI over ragged/compressed chunks to catch a
  *silent* per-op fallback, and show coarse tools are blind to it (the motivating Figure 1).

## Relaunch a GDS node (from the snapshot image)
```bash
/opt/gds-tools/mount_gds_nvme.sh        # the only per-instance step (mount local NVMe -o data=ordered)
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate
```
Details: `chameleon/launch-from-snapshot-README.md` and `chameleon/README.md`.

## Conventions / hard-won gotchas
- **Commits:** do NOT add `Co-Authored-By` or any AI-attribution trailers.
- **Progress:** keep the running `CHAMELEON-GDS-BRINGUP-LOG.md` updated during long tasks.
- **GDS:** do NOT disable ACS (it destabilized P2P and dropped an NVMe controller — `amd_iommu=off`
  is enough). ext4 must be mounted `-o data=ordered` (cuFile verifies it). GDS **reads** are safe;
  GDS **writes** are riskier (first surfaced the controller drop) — create test files with `dd`.
- **Shared NVMe:** the local NVMe disks hold other tenants' data — never reformat; use a scratch subdir.
- **gdscheck checks:** never `gdscheck | grep -q` under `set -o pipefail` (grep-q early-exit → SIGPIPE
  → false failure); capture to a var and grep a here-string.
