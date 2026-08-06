# GDSight

A per-operation, cross-layer tracer correlating **cuFile** (userspace) ↔ **nvidia-fs** (kernel) ↔
**NVMe**, to attribute GPUDirect Storage (GDS) pathologies — silent POSIX fallback, I/O
amplification, tail interference — to the application operation that caused them.

GDS bypasses the syscall layer, which removes the identity that would tie an application read to the
device work it causes. GDSight rebuilds that link per operation from two independent bases: a
`corr_id` minted at the cuFile boundary (valid only on the same thread) and a physical-address basis
that maps each operation's byte range through FIEMAP to device LBAs. The address basis is
time-independent, so it attributes asynchronous submissions that time-based correlation cannot.

Built as three eBPF plugins (`cufile`, `nvidiafs`, `block`) on the
[DataCrumbs](https://github.com/izzet/datacrumbs) substrate, emitting the
[DFTracer](https://github.com/izzet/dftracer) format. No source changes, no library injection, and
no change to the installed GDS stack.

## Repo map

| path | what |
|---|---|
| `tools/` | tracer plugins, offline attribution and scoring, figure generation |
| `workloads/` | GDS workload drivers (KvikIO, NIXL, HDF5, DALI, gdsio harnesses) |
| `chameleon/` | bring-up and smoke toolkit for a Chameleon bare-metal GPU node |
| `results/` | measurement outputs and the analysis notes behind each result |
| `results/xlayer/` | the cross-layer attribution evidence trail, one note per experiment |
| `docs/` | bring-up and build worklogs, plus background analysis |
| `external/` | pinned submodules for the substrate, analyzer and evaluated stacks |

## Reproducing

True GDS needs a qualified stack. On Chameleon a reusable image boots GDS-ready (open NVIDIA driver
560.35.05, `linux-nvidia` 6.8.0-1051 with GDS-patched nvme, nvidia-fs 2.28.4, `amd_iommu=off`). The
only per-instance step is mounting the local NVMe `-o data=ordered`, which cuFile verifies:

```bash
git clone --recurse-submodules https://github.com/izzet/gdsight.git ~/projects/gdstrace
/opt/gds-tools/mount_gds_nvme.sh                                      # mount local NVMe data=ordered
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate                                     # kvikio / cupy / DALI
```

Full relaunch guide: `chameleon/launch-from-snapshot-README.md`. Hard-won gotchas — notably **do not
disable ACS**, and never `gdscheck | grep -q` under `set -o pipefail` — are in `chameleon/README.md`
and `docs/CHAMELEON-GDS-BRINGUP-LOG.md`.

## Status

True GDS validated on Chameleon bare-metal A100. The cross-layer attribution is implemented and
evaluated across KvikIO, HDF5 and NIXL, plus controlled write, contention and command-rate cases;
every quantitative result is traced to a committed artifact in
`results/xlayer/paper-claims-audit-2026-08-05.md`.

## License

MIT — see [LICENSE](LICENSE). Submodules under `external/` carry their own licenses.
