# GDSight on the kernel PCI P2PDMA transport

The paper measures GDS on the nvidia-fs transport (CUDA 12.6). From CUDA 12.8, GDS can also use the upstream
kernel PCI P2PDMA transport, which does not go through nvidia-fs. This directory records GDSight on that
transport, on two Chameleon `compute_liqid` nodes (A100-PCIE-40GB, Samsung MZ1LB3T8HMLA NVMe, kernel
`6.8.0-1051-nvidia`, IOMMU off) with driver 570.211.01, CUDA 12.8.2 and cuFile 1.13.1.3.

| run | what it covers |
|---|---|
| [`liqid05/`](liqid05/README.md), 2026-11-17 to 18 | stack upgrade and P2PDMA enablement, the marker search, GDSight per-op traces on P2PDMA (`nv-legate` VFD) |
| [`liqid07/`](liqid07/README.md), 2026-11-27 | the HDF5 case with both VFD forks (`hpc-io/vfd-gds` 1.0.3 and `nv-legate`), on nvidia-fs with cuFile 1.13 and then on P2PDMA, with syscall counts |

## Results

1. **The nvidia-fs counters are blind on P2PDMA.** The nvidia-fs read counter is 0 for every HDF5
   configuration, so direct reads and host-staged reads look the same there (liqid05, liqid07).
2. **`pci_p2pdma_map_segment` replaces the nvidia-fs marker.** It is 0 in every host-staged case and matches
   `nvfs_get_p2p_dma_mapping` on direct reads: HDF5 cache off 257, contiguous 72 (liqid05). GDSight ships it as
   a separate `p2pdma` plugin.
3. **Per-op attribution works unchanged.** Every P2PDMA event carries the `corr_id` of a real `cuFileRead`
   (liqid05).
4. **The HDF5 finding holds on P2PDMA,** on both VFD forks: with the chunk cache on, there are no P2P mappings
   and every chunk read lands in host memory (liqid05, liqid07).
5. **Direct reads become `pread64` calls.** On P2PDMA cuFile issues a direct read as an `O_DIRECT` `pread64`
   into GPU memory, so a plain `pread64` count no longer separates the HDF5 cases; counting by destination
   buffer does (liqid07).
6. **These runs do not compare transport performance.** On liqid05, `gdsio` read at the same rate on both
   transports (1 MiB reads, 2.93 GiB/s), because the drive is the limit. On liqid07 the direct HDF5 reads
   were 0–8% faster on P2PDMA than on nvidia-fs, from three reps on one drive.

| HDF5, 64 MiB, 256 KiB chunks | cuFileRead | nvidia-fs path: nvfs_io | P2PDMA path: pci_p2pdma_map_segment |
|---|--:|--:|--:|
| chunk cache ON (default) | 262 | 0 | 0 |
| chunk cache OFF | 262 | 256 | 257 |
| contiguous | 8 | 72 | 72 |

(nv-legate VFD, liqid05; liqid07 gives the same mapping counts, and 68 for contiguous with hpc-io.)

## Using it

- `chameleon/enable_p2pdma.sh [--revert]` switches a node between the two transports (reboot after). It needs
  CUDA 12.8+, an R570+ open driver and the steps in [`liqid05/`](liqid05/README.md) ("What it took").
- `chameleon/build_datacrumbs_p2pdma.sh` builds the P2PDMA tracer install; `tools/trace_hdf5_gds_p2pdma.sh`
  traces the HDF5 case on it.

## Not covered

- NIXL, unaligned writes, tail contention and the command cap have not been rerun on P2PDMA.
- IOMMU on.
- GDSight traces with the `hpc-io` VFD (liqid07 used counters and strace only).
