# GDSight

A per-operation, cross-layer tracer correlating **cuFile** (userspace), **nvidia-fs** (kernel) and
**NVMe** (block layer), to attribute GPUDirect Storage (GDS) pathologies to the application
operation that caused them.

GDS copies NVMe data straight into GPU memory, bypassing the CPU data path and with it the identity
that links an application read to the device work it causes. The file is `O_DIRECT`, the DMA target
is GPU memory, and the command completes on a kernel thread, so none of the keys host-I/O tracing
relies on survive. GDSight rebuilds the link per operation from two independent bases:

- a **`corr_id`** minted at the cuFile boundary and carried to the kernel probes, valid only while
  the issuing thread is blocked, and
- an **address** basis that maps each operation's byte range through FIEMAP to device LBAs.

The address basis is time-independent, so it attributes asynchronous submissions that time-based
correlation cannot, including at batch=1, which makes the failure a property of asynchrony rather
than concurrency. Composed address-first, with time breaking a tie only when its key is provably
valid, the two cover every regime but one characterized corner, which is reported as a set rather
than guessed.

Built as three eBPF plugins (`cufile`, `nvidiafs`, `block`) on the
[DataCrumbs](https://github.com/izzet/datacrumbs) substrate, emitting the
[DFTracer](https://github.com/llnl/dftracer) format. It needs no source changes, no library
injection, and no change to the installed GDS stack.

## Paper

> Izzet Yildirim, Xian-He Sun, Anthony Kougkas.
> **GDSight: Per-Operation Cross-Layer Attribution for GPUDirect Storage.**
> 11th International Parallel Data Systems Workshop (PDSW'26), held in conjunction with
> SC26: The International Conference for High Performance Computing, Networking, Storage,
> and Analysis, McCormick Place Convention Center, Chicago, IL, USA, 16 November 2026.

Use [`CITATION.cff`](CITATION.cff) to cite the software and paper.
The frozen software artifact is archived at
[doi:10.5281/zenodo.22718933](https://doi.org/10.5281/zenodo.22718933).

## What it finds

Measured on unmodified stacks, in each case while the observers a practitioner would reach for
report health:

| case | what GDSight attributes |
|---|---|
| KvikIO retrieval mix | 91% of operations bypass GDS entirely, and those reads cost 2.67x at the device |
| HDF5 via the GDS VFD | `nvfs_io=0`, so the chunk cache host-stages every read, at 27% more host CPU than true GDS |
| NIXL KV-cache reads | a 4 KiB filesystem grid rounds every 2048 B read to 4x device bytes |
| unaligned writes | 46,956 device commands, of which 14,194 are reads, from read-modify-write on a pure-write workload |
| small reads under contention | p99 inflates 22x while the median moves 9%, from device contention: 3–8 overlapping large commands per slow operation |
| high command rate | nothing is wrong, since raising the block cap buys no improvement and the attributed answer is to leave it alone |

Each diagnosis resolves to a specific layout or configuration change, measured before and after.

## Repo map

| path | what |
|---|---|
| `tools/` | tracer plugins, offline attribution and scoring, experiment drivers, figure generation |
| `workloads/` | GDS workload drivers (KvikIO, NIXL, HDF5, DALI, `gdsio` harnesses) |
| `chameleon/` | bring-up and smoke toolkit for a Chameleon bare-metal GPU node |
| `results/` | measurement outputs, one directory per experiment family |
| `results/xlayer/` | the cross-layer attribution evidence trail, one note per experiment |
| `docs/` | bring-up and build worklogs, plus background analysis |
| `external/` | pinned submodules for the substrate, analyzer and evaluated stacks; see [release manifest](RELEASE-MANIFEST.md) |

## Reproducing

True GDS requires a qualified stack. On Chameleon a reusable image boots GDS-ready with the open
NVIDIA driver 560.35.05, `linux-nvidia` 6.8.0-1051 carrying the GDS-patched nvme module, nvidia-fs
2.28.4, and `amd_iommu=off`. The only per-instance step is mounting the local NVMe `-o data=ordered`,
which cuFile verifies:

```bash
git clone --recurse-submodules https://github.com/izzet/gdsight.git && cd gdsight
/opt/gds-tools/mount_gds_nvme.sh                                      # mount local NVMe data=ordered
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate                                     # kvikio / cupy / DALI
bash chameleon/setup_datacrumbs_runtime.sh                            # eBPF caps + /var/run/datacrumbs
```

The last step is required before any traced run. Without it the tracer exits silently and traced
runs produce empty traces.

Several helper scripts currently resolve paths against `$HOME/projects/gdstrace`, so either check
out there or adjust the path at the top of the scripts you run.

Each case has its own driver:

```bash
bash tools/run_ucb_kvikio_fix.sh      # KvikIO bypass, before and after coalescing
bash tools/run_hdf5_gds.sh            # HDF5 chunk cache and the rdcc sweep
bash tools/run_nixl_crosscheck.sh     # NIXL keystone: every observer against per-op attribution
bash tools/run_write_keystone.sh      # unaligned writes and read-modify-write attribution
bash tools/oracle_run.sh              # attribution against disjoint-slot ground truth
```

`chameleon/launch-from-snapshot-README.md` covers relaunching from the snapshot image.
`chameleon/README.md` and `docs/CHAMELEON-GDS-BRINGUP-LOG.md` record the operational constraints
found during bring-up, including that ACS must remain enabled and that `gdscheck` output must not be
piped to `grep -q` under `set -o pipefail`.

## License

MIT, see [LICENSE](LICENSE). Submodules under `external/` carry their own licenses.
