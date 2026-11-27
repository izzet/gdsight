# HDF5 with GPUDirect Storage: the chunk cache bypasses GDS without any report

A per-op cross-layer measurement of HDF5 reads over the GPUDirect Storage VFD (`vfd-gds`). Related
notes: `necessity.md`, `nixl-e2e-complete.md`. Reproduce with `chameleon/build_hdf5_gds.sh`,
`tools/run_hdf5_gds.sh`, `tools/trace_hdf5_gds.sh`; data in `hdf5_gds_trace.csv`,
`hdf5_gds_sweep.csv`, `hdf5_gds_cpu.csv`.

## Setup
- HW: Chameleon bare-metal A100-PCIE-40GB, Samsung PM983-class NVMe (ext4 `data=ordered`), CUDA 12.6,
  nvidia-fs 2.28, GDS 1.11.1 (libcufile; nvidia-fs stats header prints 1.17.1), kernel `6.8.0-1051-nvidia`, IOMMU off.
- SW: HDF5 1.14.5 (serial) + the GPUDirect Storage VFD, which descends from Ravi/Byna/Koziol's PDSW'20
  "GPU Direct I/O with HDF5" driver.
  - **Default: `hpc-io/vfd-gds` 1.0.3**, the maintained upstream (release 2026-03-25). It does not open
    `O_DIRECT` itself; cuFile reopens the file `O_DIRECT` internally.
  - **Previous implementation: `nv-legate/vfd-gds`**, NVIDIA's fork used by Legate (opens `O_DIRECT`,
    branches on device pointers), archived 2026-10-05 with its last commit in Nov 2024. **The numbers in
    this note and in the paper were measured with this fork.** The same case on hpc-io 1.0.3 is in
    [`hdf5-gds-hpc-io.md`](hdf5-gds-hpc-io.md): the finding holds; bandwidth and host CPU differ.
- Workload `workloads/hdf5_gds.c`: file created host-side with the default SEC2 VFD (no GDS writes);
  read into a GPU buffer via the GDS VFD (`H5Pset_fapl_gds`), toggling the raw-data chunk cache
  (`H5Pset_chunk_cache`). Reads only.

## Finding
An HDF5 read into GPU memory over the GPUDirect Storage VFD, on a chunked dataset (the default layout
for compressed or partial-access scientific arrays and ML sample stores), with the raw-data chunk cache
on (the HDF5 default), moves zero bytes over the device P2P path. Every chunk is read into the host
chunk cache and then copied to the GPU, so cuFile falls back to compat mode (CPU bounce) without
reporting it. The application issues normal `cuFileRead` calls and every API-level and aggregate tool
looks healthy; only a per-op cross-layer view (device P2P events = 0) shows that the device-to-GPU path
was never used.

## Per-op cross-layer trace (`hdf5_gds_trace.csv`)
Same 64 MiB dataset, 256 KiB chunks, traced through DataCrumbs (cuFile uprobe + nvidia-fs / NVMe kprobes):

| case | `cuFileRead` (app) | `nvfs_io` (kernel P2P) | `p2p_map` | verdict |
|---|--:|--:|--:|---|
| chunked, chunk-cache ON (HDF5 default) | 262 | 0 | 0 | silent compat — 0 P2P |
| chunked, chunk-cache OFF (the fix) | 262 | 256 | 257 | true GDS (256 chunks P2P) |
| contiguous | 8 | 72 | 136 | true GDS |

The application layer is identical in both chunked cases (262 `cuFileRead` calls), while the kernel
layer differs entirely (`nvfs_io` 0 vs 256). API-level observers (`gds_stats`, `cuFileGetStats`,
Nsight) see the same 262 cuFile ops and cannot distinguish a silent-compat run from a true-GDS run.
Attributing the device layer per op separates them.

## Untraced signal (`tools/run_hdf5_gds.sh`, `nvidia-fs Reads` counter)
Requires `rw_stats_enabled=1`. The aggregate GDS read counter gives the same verdict without the
tracer: chunked cache-ON `+0` GDS reads; cache-OFF `+256`; contiguous `+72`.

## Mechanism: the bypass threshold is the chunk-cache size (`hdf5_gds_sweep.csv`)
256 MiB dataset, cache-ON, chunk size swept. The default raw-data chunk cache is 1 MiB. A chunk that
fits in it is staged through host memory (silent compat); a chunk larger than it bypasses the cache
(true GDS):

| chunk | cache-ON GDS reads | cache-ON verdict | cache-ON BW | cache-OFF GDS reads | cache-OFF BW |
|---|--:|---|--:|--:|--:|
| 64 KiB | 0 | silent compat | 0.504 | 4096 | 0.135 |
| 256 KiB | 0 | silent compat | 0.874 | 1024 | 0.582 |
| 1 MiB | 0 | silent compat | 1.368 | 512 | 1.123 |
| 2 MiB | 384 | true GDS | 1.463 | 384 | 1.457 |
| 4 MiB | 320 | true GDS | 1.402 | 320 | 1.744 |

The silent bypass occurs exactly in the small-chunk regime (the compression / partial-access layout) and
disappears once chunks exceed the cache. The threshold is precise and observable at the device.

## Cost (`hdf5_gds_cpu.csv`)
At small chunks, the bypass does not cost bandwidth. 512 MiB, median of 3:

| case | CPU (user+sys) | %CPU | BW (GiB/s) |
|---|--:|--:|--:|
| chunked 256 KiB, cache ON (compat bounce) | 1.28 s | 54% | 0.885 |
| chunked 256 KiB, cache OFF (true GDS) | 1.01 s | 38% | 0.582 |
| contiguous (true GDS) | 0.72 s | 35% | 1.912 |

1. The cost of the silent bypass is host CPU and memory bandwidth, not throughput. Compat bounces every
   chunk through host memory, costing +27% CPU (1.28 vs 1.01 s) and +16 pp %CPU for the same 512 MiB.
   This gives up the host offload that GDS exists to provide.
2. Disabling the chunk cache to force GDS engages true P2P but is slower at small chunks (0.582 vs
   0.885 GiB/s; 4096 individual per-chunk `cuFileRead`s make it submission-bound). This mirrors the
   NIXL alignment case: the API-visible lever moves the wrong dimension.
3. The effective lever is layout. Contiguous (or chunks ≥ cache size) gives true GDS, ~2× the
   throughput, and the lowest CPU.

A per-op cross-layer view supplies all three facts: (a) the reads are silently in compat
(`nvfs_io=0`), (b) forcing GDS does not fix throughput, (c) layout is the lever. `gds_stats`, `iostat`
and Nsight supply none of them.

## Observability by tool
- `gds_stats` / `cuFileGetStats`: sees 262 cuFile ops, err=0 ("healthy"). Does not show the 0-P2P fallback.
- `iostat` / diskstats: sees device reads (aggregate), cannot tie them to the HDF5 read or say P2P vs bounce.
- nvidia-fs aggregate (`/proc/driver/nvidia-fs/stats`): the `Reads n=` counter does move, but only in
  aggregate, with no per-op or per-dataset identity, and only if `rw_stats_enabled=1`.
- GDSight: per op, `nvfs_io = 0` vs `256`, which identifies the bypass and its cause (the chunk cache).

## Scope
- Magnitudes are single-drive / single-GPU (NVMe-queue-bound): the CPU cost is real but modest (+27%).
  On multi-GPU / multi-NVMe over a shared PCIe fabric the host-bounce cost would be larger (host-BW
  bound). This is future work, consistent with the paper's other single-drive null results.
- Staging through the chunk cache is expected in hindsight, since the raw-data cache lives in host
  memory. What this note records is that the staging is not reported by any shipping GDS tool and that
  a per-op cross-layer view attributes it, on real middleware, on both the upstream VFD and NVIDIA's
  fork.
- On the default VFD, `hpc-io/vfd-gds` 1.0.3, reads go direct exactly where they did on nv-legate, and
  the default chunk cache stages them through host memory the same way. The fork changes how staged
  reads are issued (no `O_DIRECT` on open, every read goes through cuFile), so the bandwidth and CPU
  magnitudes above are nv-legate's: the "~2× throughput for contiguous" ratio does not hold on hpc-io,
  and its host-CPU cost of staging is +16% over cache off (+28% over contiguous) rather than +27%.
  See [`hdf5-gds-hpc-io.md`](hdf5-gds-hpc-io.md).

## Relation to prior work
Ravi et al. (PDSW'20) enabled GDS in HDF5 through this VFD. Their microbenchmarks showed GDS losing on
small reads and winning on large ones, with the guidance to use GDS for large I/O. GDSight observes
beneath the same VFD, per op: which reads bypass GDS, why (the chunk cache), and which lever (layout)
fixes it.

## Reproduce
```
bash chameleon/build_hdf5_gds.sh          # HDF5 1.14.5 + hpc-io/vfd-gds 1.0.3 -> ~/hdf5-gds, ~/vfd-gds
# for the published numbers, build the previous implementation's VFD instead:
#   VFD_FORK=nv-legate bash chameleon/build_hdf5_gds.sh   (FORCE=1 to replace an existing hpc-io build)
bash tools/run_hdf5_gds.sh 64 256         # A/B via nvidia-fs Reads counter (needs rw_stats_enabled=1)
bash tools/trace_hdf5_gds.sh              # GDSight cross-layer trace: cuFileRead vs nvfs_io per case
```
