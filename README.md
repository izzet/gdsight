# GDSight

A per-operation, cross-layer tracer correlating **cuFile** (userspace) ↔ **nvidia-fs** (kernel) ↔
**NVMe**, to attribute GPUDirect Storage (GDS) pathologies to the application operation that caused
them.

GDS copies NVMe data straight into GPU memory, bypassing the CPU data path — and with it the identity
that links an application read to the device work it causes. The file is `O_DIRECT`, the DMA target
is GPU memory, and the command completes on a kernel thread, so none of the keys host-I/O tracing
relies on survive. GDSight rebuilds the link per operation from two independent bases:

- a **`corr_id`** minted at the cuFile boundary and carried to the kernel probes, valid only while
  the issuing thread is blocked, and
- an **address** basis that maps each operation's byte range through FIEMAP to device LBAs.

The address basis is time-independent, so it attributes asynchronous submissions that time-based
correlation cannot — including at batch=1, which makes the failure a property of asynchrony rather
than concurrency. Composed address-first, with time breaking a tie only when its key is provably
valid, the two cover every regime but one characterized corner, which is reported as a set rather
than guessed.

## What it finds

Measured on unmodified stacks, with every observer a practitioner would reach for reporting health:

| case | what GDSight attributes |
|---|---|
| KvikIO retrieval mix | 91% of operations bypass GDS entirely, and those reads cost 2.67× at the device |
| HDF5 via the GDS VFD | `nvfs_io=0` — the chunk cache host-stages every read, at 27% *more* host CPU than true GDS |
| NIXL KV-cache reads | a 4 KiB filesystem grid rounds every 2048 B read to 4× device bytes |
| unaligned writes | 46,956 device commands, of which 14,194 are *reads* — read-modify-write on a pure-write workload |
| small reads under contention | p99 inflates 22× while the median moves 9%, from head-of-line blocking at the device queue |
| high command rate | nothing is wrong — raising the block cap buys no improvement, and the attributed answer is to leave it alone |

Each diagnosis resolves to a specific layout or configuration change, measured before and after.

Built as three eBPF plugins (`cufile`, `nvidiafs`, `block`) on the
[DataCrumbs](https://github.com/izzet/datacrumbs) substrate, emitting the
[DFTracer](https://github.com/izzet/dftracer) format. No source changes, no library injection, and
no change to the installed GDS stack.

## Repo map

| path | what |
|---|---|
| `tools/` | tracer plugins, offline attribution and scoring, experiment drivers, figure generation |
| `workloads/` | GDS workload drivers (KvikIO, NIXL, HDF5, DALI, `gdsio` harnesses) |
| `chameleon/` | bring-up and smoke toolkit for a Chameleon bare-metal GPU node |
| `results/` | measurement outputs, one directory per experiment family |
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
bash chameleon/setup_datacrumbs_runtime.sh                            # eBPF caps + /var/run/datacrumbs
```

That last step is mandatory before any traced run — without it the tracer dies silently and traced
runs yield empty traces. Each case then has its own driver, for example:

```bash
bash tools/run_ucb_kvikio_fix.sh      # KvikIO bypass, before and after coalescing
bash tools/run_hdf5_gds.sh            # HDF5 chunk cache and the rdcc sweep
bash tools/run_nixl_crosscheck.sh     # NIXL keystone: every observer vs per-op attribution
bash tools/run_write_keystone.sh      # unaligned writes and read-modify-write attribution
bash tools/oracle_run.sh              # attribution against disjoint-slot ground truth
```

Full relaunch guide: `chameleon/launch-from-snapshot-README.md`. Hard-won gotchas — notably **do not
disable ACS**, and never `gdscheck | grep -q` under `set -o pipefail` — are in `chameleon/README.md`
and `docs/CHAMELEON-GDS-BRINGUP-LOG.md`.

## Provenance

Every quantitative result is traced to a committed artifact and recomputed from raw per-repetition
data where one exists. The audit, including the defects it found and how each was resolved, is in
`results/xlayer/paper-claims-audit-2026-08-05.md`. Rate-based claims carry repetitions with
variance; structural counts (command counts, attribution fractions) are deterministic single runs.

## Status

True GDS validated on Chameleon bare-metal A100. Cross-layer attribution is implemented and
evaluated across KvikIO, HDF5 and NIXL, plus controlled write, contention and command-rate cases.

## License

MIT — see [LICENSE](LICENSE). Submodules under `external/` carry their own licenses.
