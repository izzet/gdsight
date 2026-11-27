# End-to-end NIXL GDS evaluation: observer cross-check

**Summary.** On the NVIDIA NIXL transfer engine doing true P2P GDS reads, the device read amplification
of small KV reads is invisible to every aggregate observer, cannot be attributed by timing-based
correlation, and is too fine for the cuFile API layer. Only per-NVMe-command physical address (LBA)
attribution recovers it, which makes per-op cross-layer LBA attribution necessary rather than a
convenience.

## Setup

Scripts: `chameleon/setup_nixl_venv.sh`, `workloads/nixl_gds_kv.py`,
`tools/run_nixl_crosscheck.sh`, `tools/run_nixl_kvsweep.sh`, `tools/run_nixl_e2e.sh`.

Engine: NIXL 1.3.0 (`nixl-cu12` PyPI), `nixl_agent` + GDS (`cuda_gds`) backend, batched
(`cuFileBatchIOSubmit`, batch 64), unmodified, traced under DATACRUMBS trace-all.

Tracing note: `LD_PRELOAD` the system libcufile so NIXL binds the `.so` the GDSight uprobe sits on.
Otherwise the wheel loads its bundled cu13 libcufile (a different inode) and the cuFile-API uprobe
misses; the kernel nvfs/nvme probes fire either way.

## 1. Cross-check: one run, every observer

Workload: 200 x 1 MiB pages (class A) + 2000 x 2560 B Llama-3.1-70B PP8 KV reads (class B).

| # | Observer (how an operator would look today) | What it reports for this run | Localizes the KV amplification? |
|---|---|---|---|
| A | nvidia-fs `/proc` stats (kernel GDS counters; `gds_stats`-class), telemetry ON | `Reads n=2200, readMiB=215` | No: aggregate total; 215 MiB GDS / 205 MiB asked = 1.05x, which looks healthy |
| B | `iostat` / `/proc/diskstats` (nvme1n1) | `+215 MiB` device reads | No: aggregate device bytes; cannot split class A vs B |
| C | cuFile API (`cuFileGetStats` / Nsight, per-call) | 35 `cuFileBatchIOSubmit` ops | No: 63x too coarse; 35 API calls drove 2208 device reads, and batches hide per-read behaviour |
| D | corr_id (timing/handle correlation, the natural eBPF approach) | ~87% of device cmds unattributed (3-run mean: 12%±2 correct, 87%±2 unattributed) | No: the async batch returns before the device I/O fires, so the correlation window closes |
| E | GDSight per-NVMe-cmd LBA (FIEMAP reverse-map) | class A = 1.00x, class B = 3.20x | Yes: localizes the 3.2x to the small KV reads |

Device truth for the same run (E): requested A=200 MiB / B=4.88 MiB; device A=200 MiB / B=15.62 MiB.
True P2P confirmed (`nvfs_io`=2200, `nvfs_get_p2p_dma_mapping`=2200, `nvme_setup_cmd`=2208).

### Why each observer fails

- **A, B (aggregate counters).** They sum over all ops. The 10.7 MiB of class-B waste is swamped by
  200 MiB of clean class-A reads, so the whole job reads 1.05x of what it asked, which looks benign.
  No aggregate counter, kernel or block, carries the per-op identity needed to attribute the waste.
- **C (cuFile API).** NIXL submits 64 reads per `cuFileBatchIOSubmit`, so the API (and
  `cuFileGetStats`) is batch-granular by construction: 35 calls for 2208 device reads. Per-read
  amplification is structurally unobservable at this layer regardless of the API profiler.
- **D (timing correlation).** The batch submit is async: it returns before the kernel issues the
  device reads, so the per-thread correlation window has closed by the time the NVMe commands fire,
  and 87% land with no active corr_id. Even an idealized corr_id spanning submit to completion would
  still be batch-granular (64 reads share one id) and could never separate per-read behaviour. Timing
  and handle correlation are limited to batch granularity here.
- **E (LBA).** Every NVMe command carries its physical sector; FIEMAP reverse-maps it to a file
  offset, and hence to a class. The address is intrinsic to the command and independent of timing,
  batching, and the API surface, so it is the only attributor that survives async batched GDS.

## 2. KV-size sweep

`tools/run_nixl_kvsweep.sh` drives real NIXL inference KV I/O sizes for Llama-3.1-70B across
model-parallel configurations through the engine:

| KV read size | config | requested | device (LBA) | A_byte | corr_id unattributed |
|---|---|--:|--:|--:|--:|
| 256 B | layer-access | 0.49 MiB | 7.81 MiB | 16.0x | 83% |
| 1280 B | PP16 | 2.44 MiB | 15.62 MiB | 6.4x | 91% |
| 2560 B | PP8 | 4.88 MiB | 15.62 MiB | 3.2x | 90% |
| 10240 B | PP2 | 19.53 MiB | 31.25 MiB | 1.6x | 89% |

Every A_byte equals the closed form A_byte = ⌈(off mod 4K + size)/4K⌉ · 4K / size to the digit (no
fitted parameters). The amplification is the block layer's 4 KiB read rounding, measured per op on the
real engine.

Consequence: pipeline-parallel sharding granularity, which sets the KV read size, directly trades off
device read amplification. Finer sharding (256 B layer-access) uses 16x the NVMe bandwidth it needs.
This per-op, per-class result is not available from aggregate tools.

corr_id collapses (83-91% unattributed) at all sizes, so the timing limit is structural, not a tuning
artifact.

## 3. Batch-size sweep: the two observer failures have different causes

`tools/run_nixl_batchsweep.sh` varies NIXL's GDS batch size at a fixed KV size (2560 B, PP8):

| batch | cuFileBatchIOSubmit (API ops) | device reads | API coarsening | class-B A_byte (LBA) | corr_id correct | corr_id unattr |
|--:|--:|--:|--:|--:|--:|--:|
| 1 | 2200 | 2206 | 1x | 3.200 | 0% | 100% |
| 8 | 275 | 2206 | 8x | 3.200 | 3% | 97% |
| 32 | 69 | 2206 | 32x | 3.200 | 6% | 93% |
| 64 | 35 | 2206 | 63x | 3.200 | 9% | 90% |
| 128 | 18 | 2207 | 123x | 3.200 | 8% | 91% |

A second recorded table from the same sweep (the corr_id columns differ at batch 8 and 32):

| batch | cuFile API ops | API coarsening | B A_byte (LBA) | corr_id correct / unattributed |
|--:|--:|--:|--:|--:|
| 1 | 2200 | 1x / op | 3.200x | 0% / 100% |
| 8 | 275 | 8x / op | 3.200x | 1% / 99% |
| 32 | 69 | 32x / op | 3.200x | 10% / 89% |
| 64 | 35 | 63x / op | 3.200x | 9% / 90% |
| 128 | 18 | 123x / op | 3.200x | 8% / 91% |

Results:

1. **cuFile API coarsening is batch-driven.** The API-to-device op ratio tracks batch size exactly
   (1x to 123x per op), so a per-call API profiler (`cuFileGetStats`/Nsight) is only as fine as the
   batch; at batch 128 it sees 18 calls for 2208 device reads. Per-read behaviour is structurally
   invisible at the API and gets worse the more the engine batches.
2. **corr_id collapse is async-driven, not batch-driven.** It is ~90-100% unattributed at every batch
   size, including batch=1, where the API is 1:1 with the device (2200 submits, 2206 reads). Even one
   read per submit cannot be timing-attributed, because the GDS batch submit is asynchronous: it
   returns, and the per-thread correlation window closes, before the NVMe command is issued. Asynchrony
   breaks timing attribution; batching additionally breaks the API layer.
3. **Address-based attribution is invariant.** Class-B A_byte is exactly 3.200x at every batch size.

The two failure causes are independent and cannot be tuned away. Batch=1 makes the API fine-grained,
but timing still fails (async, 100% unattributed). Raising the batch for throughput (the production
default; NIXL uses 64-128) also makes the API blind (63-123x coarse). Only per-NVMe-command
physical-address attribution is orthogonal to both the API surface and the submit/issue timing, so it
is the only observer that works in the engine's async-batched regime. Changing batch size or using a
better API profiler does not help.

## 4. Robustness

- Per-class LBA is deterministic: A=1.000x, B=3.200x identical across 3 repeats (closed form; same
  offsets give the same device rounding).
- corr_id collapse is consistent: 3 repeats at PP8 give 10/11/14% correct, 89/88/85% unattributed.
- Synthetic and real runs agree: these real-engine numbers match the synthetic NIXL-sized replay
  (Result 3) exactly (aggregate 1.052x, class-B 3.20x), validating both.

## 5. Scope

- Single-agent NIXL transfer with a NIXL-derived access pattern (real engine, real backend, real KV
  sizes), not a captured multi-node prefill-to-decode `kvbench` trace (distributed/etcd/torch; future
  work). The transfer engine and its GDS path are real; the access pattern is driven by the workload
  script.
- `cupy` is used only to allocate and register VRAM (its compute kernels JIT-fail on this driver,
  which does not affect the GDS path).
- The nvidia-fs `Ops: BatchIO` counter stays 0 even with stats on; the GDS reads register under
  `Reads n=`, so the aggregate total (215 MiB) is captured fairly. The limitation is granularity, not
  a disabled counter.
