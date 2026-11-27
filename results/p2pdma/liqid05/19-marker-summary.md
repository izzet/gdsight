# A P2P marker for the kernel PCI P2PDMA transport (2026-11-18)
Method: ftrace function profiler (system-wide, no PID filter), 14 candidate functions, 6 cases, 3 reps (fprof.sh, 18-fprof-p2pdma-3reps.csv).
HDF5: 64 MiB, 256 KiB chunks. gdsio: 1 GiB, 1 MiB reads, 1 thread. Page cache dropped before each case.

## The old signal is blind on this transport (17-hdf5-ab-nvfscounter-under-p2pdma.log)
nvidia-fs Reads counter: cache ON +0, cache OFF +0, contiguous +0. True GDS and silent compat are indistinguishable.

## Result: pci_p2pdma_map_segment, identical in all 3 reps
| case | pci_p2pdma_map_segment | nvme_setup_cmd | filemap_read | iomap_dio_rw |
| idle | 0 | 0 | 151 | 0 |
| HDF5 cache ON | 0 | 286 | 436 | 10 |
| HDF5 cache OFF | 257 | 274 | 180 | 267 |
| HDF5 contiguous | 72 | 83 | 174 | 83 |
| gdsio GPU direct (-x0) | 1024 | 1027 | 175 | 1024 |
| gdsio CPU only (-x1) | 0 | 2051 | 160 | 1024 |

## Reading
- pci_p2pdma_map_segment is zero for every host-staged case and nonzero only for true P2P.
- Counts match the nvidia-fs marker on the same workloads: contiguous 72 (nvfs_io 72 on nvidia-fs), cache OFF 257 (p2p_map 256-258), gdsio 1 per 1 MiB I/O.
- So it is a drop-in replacement for probe P2 (nvfs_get_p2p_dma_mapping).
- The HDF5 finding carries over to P2PDMA: chunk cache ON gives 0 P2P mappings, with page-cache reads 436 vs 180 and direct-I/O calls 10 vs 267.
- Idle shows 0 P2P and 0 NVMe commands, so background noise does not affect these two markers. filemap_read has an idle floor (151).

## Caveats
- One node, one drive, one GPU. IOMMU off; with the IOMMU on, the dma-iommu path may map P2P segments differently. Not tested.
- Counted, not attributed: this is a per-case total, not wired into GDSight per-op attribution yet.
