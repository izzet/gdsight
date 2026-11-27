# HDF5 + GDS on the upstream `hpc-io/vfd-gds` 1.0.3

A comparison of the two GDS VFD forks on the HDF5 case of [`hdf5-gds.md`](hdf5-gds.md). The published
numbers use `nv-legate/vfd-gds`. This note records whether the finding depends on that choice.

## Setup
- Measured 2026-11-26 on Chameleon `liqid07`, the same stack as the paper: nvidia-fs transport, cuFile
  1.11, A100-PCIE-40GB, ext4 `data=ordered` on local NVMe.
- HDF5 1.14.5 (serial), the same workload (`workloads/hdf5_gds.c`): 64 MiB dataset, 256 KiB chunks, cold
  page cache, three runs per case. The counts were identical across runs.
- Forks:
  - `nv-legate/vfd-gds`: archived 2026-10-05, last commit Nov 2024. Opens the file `O_DIRECT`, sends only
    device-pointer reads to `cuFileRead`, and issues host-buffer reads with plain `pread`.
  - `hpc-io/vfd-gds` release 1.0.3 (commit `551077e`, 2026-03-25), not archived. Does not set `O_DIRECT`
    itself. cuFile reopens the file `O_DIRECT` internally: in a standalone test, a plain fd registered
    with `cuFileHandleRegister` still gave 64 nvidia-fs reads for 64 MiB.
- Built with `VFD_FORK=hpc-io bash chameleon/build_hdf5_gds.sh`.

## Results

| | nv-legate | hpc-io 1.0.3 |
|---|---|---|
| nvidia-fs reads, chunk cache on (default) | 0 | 0 (device read 64 MiB) |
| nvidia-fs reads, chunk cache off | 256 | 256 (257 mappings) |
| nvidia-fs reads, contiguous | 72 | 65 (mapping count 65 or 129, unstable) |
| shuffle filter | 0 direct | 0 direct (cache on and off) |
| `pread64` calls, on / off / contiguous | 264 / 8 / 2 | 272 / 16 / 10 |
| `cuFileRead` calls, on / off / contiguous | 262 / 262 / 8 | 270 / 270 / 9 |
| `gds_stats` reads, on / off / contiguous | 524 / 524 / 16 | 540 / 540 / 18 |
| GiB/s, cache on | 0.85–0.89 | 1.60–1.73 |
| GiB/s, cache off | 0.59–0.61 | 0.79–0.80 |
| GiB/s, contiguous | 1.53–1.77 | 1.66–1.86 |

Host CPU (user+sys) for 512 MiB, five cold runs, hpc-io 1.0.3: 0.94 s cache on, 0.81 s cache off, 0.74 s
contiguous. The staged read costs +16% over cache off and +28% over contiguous. The paper's +27% is
nv-legate only.

## What holds and what is fork-specific
- **Holds on both forks:** under the default chunk cache, reads are silently staged through host memory.
  `gds_stats` and the cuFile counters look the same as a direct run; the device sectors and the
  nvidia-fs counters show it. Turning the cache off or using a contiguous layout goes direct on both.
- **Fork-specific:**
  - Bandwidth: hpc-io reads the staged chunks buffered with readahead, while nv-legate reads them with
    `O_DIRECT`, so hpc-io is faster with the cache on (1.60–1.73 vs 0.85–0.89 GiB/s). The "contiguous
    reads at ~2× compat" ratio holds only for nv-legate.
  - The +27% host-CPU figure is nv-legate's; hpc-io shows +16% (vs cache off) and +28% (vs contiguous).

## Not tested
- GDSight itself was not run on hpc-io. These are counters only (nvidia-fs stats, device sectors,
  call counts), not per-operation attribution.
- The kernel PCI P2PDMA stack (`results/p2pdma/`) was not tested with hpc-io.
- Only hpc-io 1.0.3 was tested. An older hpc-io commit may have behaved as the earlier note claimed
  (no GDS path).

Raw outputs remain on `liqid07` under `/mnt/nvme1/gdstrace-smoke/v2` and `/v3`; they are not copied here.
