# HDF5 + GPUDirect Storage: the chunk cache silently defeats GDS (per-op cross-layer evidence)

A scientific-HPC keystone for GDSight, on real middleware (HDF5) and the community's own GDS path
(the `vfd-gds` VFD, PDSW'20 lineage). Companion to the ML keystones (`necessity.md`,
`nixl-e2e-complete.md`). Reproduce with `chameleon/build_hdf5_gds.sh`, `tools/run_hdf5_gds.sh`,
`tools/trace_hdf5_gds.sh`; data in `hdf5_gds_trace.csv`, `hdf5_gds_sweep.csv`, `hdf5_gds_cpu.csv`.

## Setup
- HW: Chameleon bare-metal A100-PCIE-40GB, Samsung PM983-class NVMe (ext4 `data=ordered`), CUDA 12.6,
  nvidia-fs 2.28, GDS 1.17.1, kernel `6.8.0-1051-nvidia`, IOMMU off.
- SW: **HDF5 1.14.5** (serial) + **`nv-legate/vfd-gds`** — NVIDIA's maintained GPUDirect-Storage VFD
  (opens `O_DIRECT`, branches on device pointers; used in production by Legate). The VFD is the direct
  descendant of Ravi/Byna/Koziol's PDSW'20 "GPU Direct I/O with HDF5" driver.
- Workload `workloads/hdf5_gds.c`: file created host-side with the default SEC2 VFD (no GDS writes);
  read into a GPU buffer via the GDS VFD (`H5Pset_fapl_gds`), toggling the raw-data chunk cache
  (`H5Pset_chunk_cache`). Reads only.

## The finding
An HDF5 read into GPU memory over the "GPUDirect Storage" VFD, on a **chunked** dataset (the default
layout for compressed / partial-access scientific arrays and ML sample stores), with the raw-data
**chunk cache on** (the HDF5 default), moves **zero** bytes over the device P2P path. Every chunk is
staged through the host chunk cache (read chunk → host memory → copy to GPU), so cuFile silently falls
back to compat mode (CPU bounce). The application issues normal `cuFileRead` calls and every API/aggregate
tool looks healthy; only a per-op cross-layer view (device P2P events = 0) reveals that "GPUDirect
Storage" never touched the device→GPU path.

## Per-op cross-layer evidence (GDSight trace; `hdf5_gds_trace.csv`)
Same 64 MiB dataset, 256 KiB chunks, traced through DataCrumbs (cuFile uprobe + nvidia-fs / NVMe kprobes):

| case | `cuFileRead` (app) | `nvfs_io` (kernel P2P) | `p2p_map` | verdict |
|---|--:|--:|--:|---|
| chunked, chunk-cache **ON** (HDF5 default) | 262 | **0** | 0 | **silent compat — 0 P2P** |
| chunked, chunk-cache **OFF** (the fix) | 262 | 256 | 257 | true GDS (256 chunks P2P) |
| contiguous | 8 | 72 | 136 | true GDS |

The crux: the **application layer is identical** — 262 `cuFileRead` calls either way — while the **kernel
layer differs entirely** (`nvfs_io` 0 vs 256). No API-level observer (`gds_stats`, `cuFileGetStats`,
Nsight) can distinguish a silent-compat run from a true-GDS run; they see the same 262 cuFile ops. Only
attributing the device layer per op separates them.

## Untraced signal (`tools/run_hdf5_gds.sh`, `nvidia-fs Reads` counter)
Requires `rw_stats_enabled=1`. Same verdict without the tracer, via the aggregate GDS read counter:
chunked cache-ON → `+0` GDS reads; cache-OFF → `+256`; contiguous → `+72`.

## The mechanism, characterized: the bypass threshold IS the chunk-cache size (`hdf5_gds_sweep.csv`)
256 MiB dataset, cache-ON, chunk size swept. The default raw-data chunk cache is 1 MiB; a chunk that
fits in it is staged through host (silent compat), a chunk larger than it bypasses the cache (true GDS):

| chunk | cache-ON GDS reads | cache-ON verdict | cache-ON BW | cache-OFF GDS reads | cache-OFF BW |
|---|--:|---|--:|--:|--:|
| 64 KiB | 0 | silent compat | 0.504 | 4096 | 0.135 |
| 256 KiB | 0 | silent compat | 0.874 | 1024 | 0.582 |
| 1 MiB | 0 | silent compat | 1.368 | 512 | 1.123 |
| 2 MiB | 384 | **true GDS** | 1.463 | 384 | 1.457 |
| 4 MiB | 320 | **true GDS** | 1.402 | 320 | 1.744 |

So the silent bypass is exactly the small-chunk regime — the compression/partial-access layout — and
disappears once chunks exceed the cache. This is a precise, device-observable threshold, not folklore.

## The "so what" — an honest, non-obvious cost (`hdf5_gds_cpu.csv`)
The naive reading ("silent bypass = lost bandwidth") is **wrong at small chunks**, and that is the point.
512 MiB, median of 3:

| case | CPU (user+sys) | %CPU | BW (GiB/s) |
|---|--:|--:|--:|
| chunked 256 KiB, cache **ON** (compat bounce) | 1.28 s | 54% | 0.885 |
| chunked 256 KiB, cache **OFF** (true GDS) | 1.01 s | 38% | 0.582 |
| contiguous (true GDS) | 0.72 s | 35% | **1.912** |

1. **The cost of the silent bypass is host CPU / memory bandwidth, not throughput.** Compat bounces every
   chunk through host memory → **+27% CPU (1.28 vs 1.01 s), +16 pp %CPU** for the same 512 MiB — forfeiting
   exactly the host-offload that GDS exists to provide.
2. **The obvious fix is a trap.** "Disable the chunk cache to force GDS" engages true P2P but is *slower*
   at small chunks (0.582 vs 0.885 GiB/s; 4096 individual per-chunk `cuFileRead`s → submission-bound).
   Mirrors the NIXL align-trap: the API-visible lever moves the wrong dimension.
3. **The real lever is layout.** Contiguous (or chunks ≥ cache size) gives true GDS **and** ~2× the
   throughput **and** the lowest CPU.

Only a per-op cross-layer view supplies all three facts: (a) you are silently in compat (`nvfs_io=0`),
(b) forcing GDS won't fix throughput (the trap), (c) layout is the lever. `gds_stats`/`iostat`/Nsight
supply none of them.

## Observability gap (who sees what)
- `gds_stats` / `cuFileGetStats`: sees 262 cuFile ops, err=0 — "healthy". Blind to the 0-P2P fallback.
- `iostat` / diskstats: sees device reads (aggregate), cannot tie them to the HDF5 read or say P2P-vs-bounce.
- nvidia-fs aggregate (`/proc/driver/nvidia-fs/stats`): the `Reads n=` counter does move — but only in
  aggregate, with no per-op / per-dataset identity, and only if `rw_stats_enabled=1`.
- GDSight: per op, `nvfs_io = 0` vs `256` — names the bypass and its cause (the chunk cache).

## Honest scope
- Magnitudes are single-drive / single-GPU (NVMe-queue-bound): the CPU tax is real but modest (+27%);
  on multi-GPU / multi-NVMe over a shared PCIe fabric the host-bounce cost would bite harder (host-BW
  bound). Stated as future work, consistent with the paper's other single-drive nulls.
- The chunk-cache→host staging is understandable in hindsight (raw-data cache lives in host memory); the
  contribution is that it is **silent** to every shipping GDS tool and that a per-op cross-layer view
  attributes it — on real middleware, on the community's production VFD.
- A second, lesser bug in the *other* fork (`hpc-io/vfd-gds`) — it never opens `O_DIRECT`, so it is
  compat even for contiguous — is noted only in passing; the primary result uses NVIDIA's fork.

## Lineage / novelty
Ravi et al. (PDSW'20) *enabled* GDS in HDF5 (the VFD); their own microbenchmarks already showed GDS
losing on small reads and winning on large, but as an enabling paper they could only say "use GDS for
large I/O." GDSight *sees beneath* the same VFD, per op: which reads silently bypass GDS, why (the chunk
cache), and which lever (layout) fixes it. Same library, same community, same venue — the next chapter.

## Reproduce
```
bash chameleon/build_hdf5_gds.sh          # HDF5 1.14.5 + nv-legate/vfd-gds -> ~/hdf5-gds, ~/vfd-gds
bash tools/run_hdf5_gds.sh 64 256         # A/B via nvidia-fs Reads counter (needs rw_stats_enabled=1)
bash tools/trace_hdf5_gds.sh              # GDSight cross-layer trace: cuFileRead vs nvfs_io per case
```
