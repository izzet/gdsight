# HDF5-GDS investigation and validation log

Log for evaluating HDF5 with `vfd-gds` and diagnosing cross-layer behavior under GPUDirect Storage.

## Overview & Key Finding
- **Core finding:** The HDF5 chunk cache silently defeats GPUDirect Storage. A chunked dataset (the default layout) read with the chunk cache enabled issues normal `cuFileRead` calls, but `nvfs_io = 0` (silent CPU bounce / compatibility path, no peer-to-peer DMA).
- **With chunk cache disabled:** True GDS is achieved (`nvfs_io = N`).
- **Contiguous datasets:** True GDS is achieved directly.
- **Cross-layer visibility:** Only GDSight's cross-layer correlation (`nvfs_io = 0`) identifies this pathology; coarse tools like `gds_stats` and `iostat` appear healthy.

## Environment verification
- Chameleon bare-metal node (A100-PCIE-40GB, driver 560.35.05, kernel `6.8.0-1051-nvidia`, `nvidia_fs` loaded).
- NVMe formatted as ext4 (4 KiB blocks) mounted `-o data=ordered` at `/mnt/nvme1`.
- `gdscheck -p`: NVMe: Supported | IOMMU: disabled.
- eBPF capabilities configured on DataCrumbs runtime.

## HDF5 + GDS build & setup
- Built HDF5 1.14.5 (serial) and `nv-legate/vfd-gds` (production fork; opens `O_DIRECT` + device-pointer branch) via `chameleon/build_hdf5_gds.sh`.
- Build options: `-DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF` (building `libhdf5_vfd_gds.so`).
- Test workload: `workloads/hdf5_gds.c` (reads-only; file created host-side with default SEC2 VFD; GDS VFD on read; toggles `H5Pset_chunk_cache`).
- Parameter check: `/proc/driver/nvidia-fs/stats` requires `rw_stats_enabled=1` (`echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled`). The active counter is `Reads : n=… readMiB=…`.

## Empirical A/B results (64 MiB dataset, 256 KiB chunks)
Evaluated via `tools/run_hdf5_gds.sh`:

| Case | nvfs Reads Δ | Verdict |
|---|---|---|
| Chunked, chunk-cache **ON** (default) | **+0** | **Silent compatibility mode — zero P2P** |
| Chunked, chunk-cache **OFF** | +256 (+65 MiB) | True GDS (all 256 chunks P2P) |
| Contiguous | +72 (+64 MiB) | True GDS |

### Performance characteristics
At 256 KiB chunk sizes, true-GDS chunked (0.58 GiB/s) has higher per-chunk P2P submission overhead than CPU compatibility mode (0.81 GiB/s), while contiguous layout (1.58 GiB/s) outperforms both. Layout optimization (contiguous/large extents) is the effective lever.

## Cross-layer trace evidence
Cross-layer trace via `tools/trace_hdf5_gds.sh` (64 MiB / 256 KiB, `results/xlayer/hdf5_gds_trace.csv`):
- Chunked cache-ON: `cuFileRead=262, nvfs_io=0` (silent compatibility mode).
- Chunked cache-OFF: `cuFileRead=262, nvfs_io=256` (true GDS).
- Contiguous: `cuFileRead=8, nvfs_io=72`.
- The application layer appears identical (262 calls either way); only the kernel layer reveals the path divergence.

## Threshold & CPU cost analysis
- **Threshold (`hdf5_gds_sweep.csv`):** Silent bypass occurs when chunk size ≤ chunk-cache size (1 MiB default). Chunks ≤ 1 MiB fall back to compatibility mode; chunks ≥ 2 MiB engage true GDS even with the cache enabled.
- **CPU impact (`hdf5_gds_cpu.csv`, 512 MiB):** Compatibility mode incurs higher CPU overhead: 1.28 s / 54% CPU vs. true-GDS 1.01 s / 38% CPU (+27% CPU bounce overhead for compatibility mode). Contiguous layout yields 1.912 GiB/s at 35% CPU.
- Full details documented in `results/xlayer/hdf5-gds.md`.
