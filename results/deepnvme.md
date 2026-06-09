# DeepNVMe (DeepSpeed) under GDS-Trace

DeepNVMe is DeepSpeed's NVMe I/O layer behind **ZeRO-Inference/Infinity** (weight/KV/optimizer offload) and
**FastPersist** checkpointing — a real, production GDS consumer, tuned by an autotuner over five knobs
(`block_size, queue_depth, single_submit, overlap_events, intra_op_parallelism`). Install: `deepspeed-venv`,
`DS_BUILD_GDS=1 DS_BUILD_AIO=1 pip install deepspeed --no-build-isolation` (CUDA_HOME set); `ds_report` →
`async_io [OKAY]`, `gds [OKAY]`. Workloads: `external/DeepSpeedExamples/deepnvme/file_access/*`,
`workloads/deepnvme_gds_load.py` (parametrized).

## Phase 1 — traced end-to-end
`gds_load_gpu_tensor.py` (1 GB): DeepNVMe issues **one plain `cuFileRead`** (`sync_pread` of the whole file,
on a worker thread) → driver splits by `block_size` into **1024 `nvfs_get_p2p_dma_mapping` (true P2P) + 1024
`nvme_setup_cmd`** (readMiB +1024, 2.7 GB/s). Attribution **corr_id 1024/1024 + LBA 1024/1024, agree 100%**
(one op in flight → count==1 fallback). DeepNVMe uses **plain `cuFileRead`**, not the batch API NIXL uses.
(Needs the system-libcufile `LD_PRELOAD` so the uprobe fires — DeepNVMe resolves cuFile via torch's bundled
lib otherwise; kernel kprobes fire regardless.)

## Phase 2a — GDS vs AIO (same API, different backend)
| backend | `cuFileRead` | `nvfs_io`/`p2p` (GDS) | `nvme` (device) | verdict |
|---|--:|--:|--:|---|
| **GDS** (`gds_handle`) | 1 | 1024 | 1029 | **true zero-copy P2P** |
| **AIO** (`aio_handle`) | 0 | 0 | 1030 | **CPU bounce** (libaio→host→`cudaMemcpy`) |

Both move the same device bytes (~1024 cmds), but only GDS-Trace shows **which config is actually zero-copy
vs silently host-staged**. `iostat`/`gds_stats` see identical device reads; the choice is a config flag.

## Phase 2b — block_size → device-command count → throughput (explaining the autotuner)
| block_size | P2P mappings (#chunks) | NVMe cmds (device) | throughput |
|---|--:|--:|--:|
| 256 KiB | 4096 | 4101 | 2.46 GB/s |
| 1 MiB | 1024 | 1029 | **2.87 GB/s** |
| 4 MiB | 1024 | 1029 | 2.87 GB/s |
| 16 MiB | 840 | 845 | 2.85 GB/s |

Throughput is gated by **device-command count**, which `block_size` controls down to a **~1 MiB / MDTS
floor**: DeepNVMe caps chunks at ~MDTS, so ≥1 MiB saturates at ~1024 commands; 256 KiB inflates to 4096
commands (overhead-bound → 2.46 GB/s). This is *why* the autotuner converges to ~1 MiB — and GDS-Trace shows
the **device-level mechanism** that the autotuner (throughput-only) can't see.

## Phase 2c — intra_op_parallelism: no effect here (honest)
`1` vs `8`: identical 2.87 GB/s, 6 NVMe-issuing threads either way. A single large `sync_pread` is already
device-bound, so the host-parallelism knob doesn't help. (It would matter for many concurrent small ops.)

## Phase 3 — ZeRO-Inference (real inference workload, NVMe weight offload via GDS)
Ran **OPT-1.3B** with DeepSpeed ZeRO-3 `--disk-offload --offload-dir /mnt/nvme1/... --use_gds` (weights
offloaded to NVMe, streamed to GPU via GDS *every forward pass*). GDS engaged heavily: a 16-token generate
streamed **+94.5 GB via ~9800 GDS reads** (params re-fetched per forward). Traced (bounded run): **350
`cuFileRead` + 100 `cuFileWrite` (offload) → 25864 `nvfs_get_p2p_dma_mapping` (true P2P) + 25864 `nvme`**;
per-op attribution **corr_id 98.6%** (25494/25864).
- **DeepNVMe uses *sync* `cuFileRead`** (per Phase 1), so per-tid corr_id resolves even the concurrent
  layer-prefetch → the cheap always-on heuristic suffices; the LBA-specific advantage (async/thread-decoupled)
  is **not** triggered here (DeepNVMe is sync, not `cuFileReadAsync`/batch like NIXL).
- Each `cuFileRead` is a whole param tensor (~196 MB) → ~74 device commands; GDS-Trace ties each of the
  25864 device commands to its causing param read — attribution no aggregate tool (`gds_stats`/`iostat`) can do.
- Gotchas: `--offload-dir` MUST be on `/mnt/nvme1` (`data=ordered`) for GDS; the example targets an old
  transformers fork → patched `tokenizer.batch_encode_plus` → `tokenizer(...)`. Single-pass corr_id counting
  under-reports (NVMe events precede their cuFileRead's *exit* event in file order) — count two-pass.

## Takeaway & scope (Phases 1–3)
On a real, autotuned production I/O layer + a real inference workload, GDS-Trace: **(1)** traces the GDS path
end-to-end (Phase 1); **(2)** turns throughput-only tuning into **device-level explanation** — true-P2P-vs-
bounce per config, and *why* `block_size` matters (device-command count → MDTS floor) (Phase 2); **(3)**
attributes a **real ZeRO-Inference weight-stream per-op at 98.6%** (Phase 3). Honest scope: DeepNVMe is
well-engineered **and synchronous**, so it's clean and corr_id suffices — this is *real-workload per-op
attribution + explained tuning*, **not** a pathology or an LBA-win. The LBA-unique (async) regime stays the
`cuFileReadAsync`/NIXL-batch case. Reproduce: `workloads/deepnvme_gds_load.py`, the `file_access`
`gds_`/`aio_` scripts, and `run_model.py --disk-offload --use_gds`.
