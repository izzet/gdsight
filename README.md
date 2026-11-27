# GDSight

[![Checks](https://github.com/izzet/gdsight/actions/workflows/checks.yml/badge.svg)](https://github.com/izzet/gdsight/actions/workflows/checks.yml)

GDSight is a per-operation, cross-layer tracer for GPUDirect Storage (GDS). It ties each device
command back to the application read or write that caused it, across **cuFile** (userspace), the GDS
transport (**nvidia-fs**, or the kernel's **PCI P2PDMA**) and **NVMe** (block layer).

With GDS, NVMe data moves straight into GPU memory: the file is opened `O_DIRECT`, the DMA target is
GPU memory, and the command completes on a kernel thread. The keys that host-I/O tracing relies on to
link an application read to its device work are gone. GDSight rebuilds the link per operation in two
ways:

- **Time:** a `corr_id` minted at the cuFile call and carried to the kernel probes. It is exact for
  synchronous reads but fails for asynchronous ones, because the call returns before the device
  command is issued.
- **Address:** each operation's byte range, mapped through the file's extents (FIEMAP) to device
  sectors. It does not depend on timing, so it also attributes asynchronous and batched I/O.

GDSight uses the address first and falls back to time only where the time key is known to be valid.
Commands it cannot attribute to a single operation are reported as a set of candidates, not guessed.

It is built as eBPF plugins (`cufile`, `nvidiafs` or `p2pdma`, and `block`) on the
[DataCrumbs](https://github.com/llnl-asr/datacrumbs) substrate, emitting the
[DFTracer](https://github.com/llnl-asr/dftracer) format. It needs no source changes, no library
injection, and no change to the installed GDS stack.

## Paper

> Izzet Yildirim, Xian-He Sun, Anthony Kougkas.
> **GDSight: Per-Operation Cross-Layer Attribution for GPUDirect Storage.**
> 11th International Parallel Data Systems Workshop (PDSW'26), held in conjunction with
> SC26: The International Conference for High Performance Computing, Networking, Storage,
> and Analysis, McCormick Place Convention Center, Chicago, IL, USA, 16 November 2026.
> [doi:10.1109/SCW72768.2026.00303](https://doi.ieeecomputersociety.org/10.1109/SCW72768.2026.00303)

Use [`CITATION.cff`](CITATION.cff) to cite the software and paper.
The frozen software artifact is archived at
[doi:10.5281/zenodo.22718932](https://doi.org/10.5281/zenodo.22718932).

## What it finds

Measured on unmodified stacks, in each case while the observers a practitioner would reach for
report health:

| case | what GDSight attributes |
|---|---|
| KvikIO retrieval mix | 91% of operations bypass GDS entirely, and those reads cost 2.67x at the device |
| HDF5 via the GDS VFD | `nvfs_io=0`, so the chunk cache host-stages every read, at 27% more host CPU than true GDS (measured on the earlier `nv-legate` VFD; [both forks compared](results/xlayer/hdf5-gds-hpc-io.md)) |
| NIXL KV-cache reads | a 4 KiB filesystem grid rounds every 2048 B read to 4x device bytes |
| unaligned writes | 46,956 device commands, of which 14,194 are reads, from read-modify-write on a pure-write workload |
| small reads under contention | p99 inflates 22x while the median moves 9%, from device contention: 3–8 overlapping large commands per slow operation |
| high command rate | nothing is wrong, since raising the block cap buys no improvement and the attributed answer is to leave it alone |

Each diagnosis resolves to a specific layout or configuration change, measured before and after.
The paper measures the nvidia-fs transport; [`results/p2pdma/`](results/p2pdma/README.md) repeats the
HDF5 case on the kernel PCI P2PDMA transport.

## Where the code lives

The eBPF plugins are not in upstream DataCrumbs. They live in a fork, pinned here as the
`external/datacrumbs` submodule ([`izzet/datacrumbs`](https://github.com/izzet/datacrumbs/tree/feat/p2pdma),
branch `feat/p2pdma`). The offline attribution is in this repository, under [`tools/`](tools/README.md).

| layer | file | what it records or computes |
|---|---|---|
| cuFile (uprobes) | [`plugins/custom_probes/cufile/cufile.bpf.c`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/plugins/custom_probes/cufile/cufile.bpf.c) | per-op offset and size; mints the `corr_id` |
| nvidia-fs (kprobes) | [`plugins/custom_probes/nvidiafs/nvidiafs.bpf.c`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/plugins/custom_probes/nvidiafs/nvidiafs.bpf.c) | `nvfs_io`, the P2P mapping and shadow-buffer markers |
| P2PDMA (kprobe) | [`plugins/custom_probes/p2pdma/p2pdma.bpf.c`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/plugins/custom_probes/p2pdma/p2pdma.bpf.c) | `pci_p2pdma_map_segment`: the P2P marker when nvidia-fs is bypassed |
| NVMe (kprobe) | [`plugins/custom_probes/block/block.bpf.c`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/plugins/custom_probes/block/block.bpf.c) | `nvme_setup_cmd`: sector, size, direction |
| correlation | [`plugins/custom_probes/gdstrace_corr.bpf.h`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/plugins/custom_probes/gdstrace_corr.bpf.h) | the `corr_id` maps shared by the plugins |
| host configs | [`configs/izzet-gdstrace-node.yaml`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/configs/izzet-gdstrace-node.yaml), [`izzet-gdstrace-node-pcidma.yaml`](https://github.com/izzet/datacrumbs/blob/feat/p2pdma/etc/datacrumbs/configs/izzet-gdstrace-node-pcidma.yaml) | which plugins a build loads: the paper's (nvidia-fs) and the P2PDMA set |
| address basis | [`tools/lba_match.py`](tools/lba_match.py), [`tools/batch_addr_attr.py`](tools/batch_addr_attr.py), [`tools/posix_addr_attr.py`](tools/posix_addr_attr.py) | sector to FIEMAP to the covering operation |
| composition | [`tools/compose_score.py`](tools/compose_score.py) | address first; time only when its key is valid |
| validation | [`tools/oracle_score.py`](tools/oracle_score.py) | scoring against disjoint-slot ground truth |

## Repo map

| path | what |
|---|---|
| [`tools/`](tools/README.md) | offline attribution and scoring, experiment drivers, figure generation |
| [`workloads/`](workloads/README.md) | GDS workloads (cuFile C programs; KvikIO, NIXL, HDF5, DALI and other library readers) |
| [`chameleon/`](chameleon/README.md) | bring-up and smoke toolkit for a Chameleon bare-metal GPU node |
| `results/xlayer/` | one note per experiment, with its data |
| `results/figures/` | paper figures and the data behind them |
| `results/overhead/` | tracer overhead measurements |
| [`results/p2pdma/`](results/p2pdma/README.md) | GDSight on the kernel PCI P2PDMA transport |
| `results/chameleon/` | node bring-up checks |
| `external/` | pinned submodules for the substrate, analyzer and evaluated stacks; see [release manifest](RELEASE-MANIFEST.md) |
| `website/` | project website source |

## Requirements

- A GDS-capable GPU and a local NVMe drive with ext4 mounted `-o data=ordered`.
- For the nvidia-fs transport (the paper): the open NVIDIA driver, nvidia-fs, a kernel with the
  GDS-patched nvme module, and IOMMU off.
- For the PCI P2PDMA transport: CUDA 12.8 or later, the R570 or later open driver, and a kernel with
  `CONFIG_PCI_P2PDMA` ([`results/p2pdma/`](results/p2pdma/README.md) lists the steps).
- Root, for the eBPF probes and the kernel function counts.

Tested on Chameleon bare-metal nodes with one A100-PCIE-40GB and one Samsung PM983-class NVMe drive.

## Reproducing

On Chameleon, the image `grc-ub2404-nvk-gds-a100-cu126-v5` boots GDS-ready on the paper's stack: open
NVIDIA driver 560.35.05, CUDA 12.6, `linux-nvidia` 6.8.0-1051 with the GDS-patched nvme module, nvidia-fs
2.28.4, and `amd_iommu=off` ([relaunch guide](chameleon/launch-from-snapshot-README.md)). On a new
instance:

```bash
git clone --recurse-submodules https://github.com/izzet/gdsight.git && cd gdsight
/opt/gds-tools/mount_gds_nvme.sh                                      # mount local NVMe data=ordered
/usr/local/cuda-12.6/gds/tools/gdscheck -p | grep -E 'NVMe |IOMMU:'   # expect NVMe: Supported | IOMMU: disabled
source /opt/gds-venv/bin/activate                                     # kvikio / cupy / DALI
bash chameleon/build_datacrumbs.sh                                    # tracer + DFAnalyzer (once per node)
bash chameleon/setup_datacrumbs_runtime.sh                            # eBPF caps + /var/run/datacrumbs (each boot)
```

`setup_datacrumbs_runtime.sh` is required before any traced run. Without it the tracer exits silently
and traced runs produce empty traces. Two cases need one more build step:
`bash chameleon/build_hdf5_gds.sh` for HDF5 and `bash chameleon/setup_nixl_venv.sh` for NIXL.

Scripts resolve paths relative to the repository checkout; set `REPO=/path/to/gdsight` to override.
Each case has its own driver ([full list](tools/README.md)):

```bash
bash tools/run_ucb_kvikio_fix.sh      # KvikIO bypass, before and after coalescing
bash tools/run_hdf5_gds.sh            # HDF5 chunk cache on and off, and the chunk-cache size sweep
bash tools/run_nixl_crosscheck.sh     # NIXL: every observer against per-op attribution
bash tools/run_write_keystone.sh      # unaligned writes and read-modify-write attribution
bash tools/run_tail_reps.sh           # small-read tail under large-read contention (traced)
bash tools/run_tail_untraced.sh       # the same tail with no tracer in the loop
bash tools/cap_tail_reps.sh           # block command cap: tail with max_sectors_kb raised
bash tools/amp_perf.sh                # block command cap: throughput, untraced
bash tools/oracle_run.sh              # attribution against disjoint-slot ground truth
```

[`chameleon/README.md`](chameleon/README.md) records the operational constraints found during bring-up,
including that ACS must remain enabled and that `gdscheck` output must not be piped to `grep -q` under
`set -o pipefail`.

## License

MIT, see [LICENSE](LICENSE). Submodules under `external/` carry their own licenses.
