# Real end-to-end NIXL GDS eval — the irreplaceability cross-check (complete)

**One-line thesis.** On the *actual* NVIDIA NIXL transfer engine doing true P2P GDS reads, the
device read-amplification of small KV reads is **invisible to every aggregate observer, un-attributable
by timing-based correlation, and too fine for the cuFile API layer** — only per-NVMe-command physical
address (LBA) attribution recovers it. That makes per-op cross-layer LBA attribution *necessary*, not a
convenience.

Reproducible: `chameleon/setup_nixl_venv.sh`, `workloads/nixl_gds_kv.py`,
`tools/run_nixl_crosscheck.sh`, `tools/run_nixl_kvsweep.sh`, `tools/run_nixl_e2e.sh`.
Engine: NIXL 1.3.0 (`nixl-cu12` PyPI), `nixl_agent` + GDS (`cuda_gds`) backend, batched
(`cuFileBatchIOSubmit`, batch 64), unmodified, traced under DATACRUMBS trace-all.
Tracing note: `LD_PRELOAD` the **system** libcufile so NIXL binds the `.so` our uprobe sits on
(the wheel otherwise loads its bundled cu13 libcufile — different inode — and the cuFile-API uprobe
misses; kernel nvfs/nvme probes fire either way).

---

## 1. The cross-check — one real run, every observer (keystone: 200×1 MiB pages + 2000×2560 B Llama-3.1-70B PP8 KV)

| # | Observer (how an operator would look today) | What it reports for this run | Localizes the KV amplification? |
|---|---|---|---|
| A | **nvidia-fs `/proc` stats** (kernel GDS counters; `gds_stats`-class), telemetry **ON** | `Reads n=2200, readMiB=215` | ❌ aggregate total; 215 MiB GDS / 205 MiB asked = **1.05× → looks healthy** |
| B | **`iostat` / `/proc/diskstats`** (nvme1n1) | `+215 MiB` device reads | ❌ aggregate device bytes; **cannot split class A vs B** |
| C | **cuFile API** (`cuFileGetStats` / Nsight, per-call) | **35** `cuFileBatchIOSubmit` ops | ❌ **63× too coarse** — 35 API calls drove 2208 device reads; batches hide per-read |
| D | **corr_id** (timing/handle correlation — the natural eBPF approach) | **~87% of device cmds unattributed** (3-run mean: 12%±2 correct, 87%±2 unattributed) | ❌ async batch returns before the device I/O fires → correlation window closes |
| E | **ours — per-NVMe-cmd LBA** (FIEMAP reverse-map) | **class A = 1.00×, class B = 3.20×** | ✅ **localizes the 3.2× to the small KV reads** |

Same run, device truth (E): requested A=200 MiB / B=4.88 MiB; device A=200 MiB / B=15.62 MiB; true P2P
confirmed (`nvfs_io`=2200, `nvfs_get_p2p_dma_mapping`=2200, `nvme_setup_cmd`=2208).

### Why each aggregate/standard observer fails (the intellectual core)
- **A, B (aggregate counters).** They sum over all ops. The 10.7 MiB of class-B waste is swamped by
  200 MiB of clean class-A reads → the whole job reads 1.05× of what it asked, which reads as benign.
  No aggregate counter — kernel or block — carries the *per-op* identity needed to attribute the waste.
- **C (cuFile API).** NIXL submits 64 reads per `cuFileBatchIOSubmit`; the API (and `cuFileGetStats`)
  is therefore *batch-granular* by construction — 35 calls for 2208 device reads. Per-read amplification
  is structurally unobservable at this layer no matter how good the API profiler is.
- **D (timing correlation).** The batch submit is async — it returns before the kernel issues the
  device reads, so the per-thread correlation window has closed by the time the NVMe commands fire →
  87% land with no active corr_id. And even an *idealized* corr_id that spanned submit→completion would
  still be batch-granular (64 reads share one id) → it could never separate per-read. Timing/handle
  correlation is fundamentally limited to batch granularity here.
- **E (LBA).** Every NVMe command carries its physical sector; FIEMAP reverse-maps it to a file offset,
  hence to a class. Address is intrinsic to the command and independent of timing, batching, and the
  API surface — the only attributor that survives async batched GDS.

---

## 2. The amplification is a law, measured on real sizes (zero-parameter)

`tools/run_nixl_kvsweep.sh` — real NIXL inference KV I/O sizes for Llama-3.1-70B across model-parallel
configs, driven through the real engine:

| KV read size | config | requested | device (LBA) | **A_byte** | corr_id unattributed |
|---|---|--:|--:|--:|--:|
| 256 B | layer-access | 0.49 MiB | 7.81 MiB | **16.0×** | 83% |
| 1280 B | PP16 | 2.44 MiB | 15.62 MiB | **6.4×** | 91% |
| 2560 B | PP8 | 4.88 MiB | 15.62 MiB | **3.2×** | 90% |
| 10240 B | PP2 | 19.53 MiB | 31.25 MiB | **1.6×** | 89% |

Every A_byte equals the closed form **A_byte = ⌈(off mod 4K + size)/4K⌉ · 4K / size** to the digit →
the amplification is the block layer's 4 KiB read-rounding, and our per-op tool measures it on the real
engine. **Actionable consequence:** pipeline-parallel sharding granularity (which sets the KV read
size) directly trades off device read-amplification — finer sharding (256 B layer-access) burns 16× the
NVMe bandwidth it needs. This is a per-op, per-class insight no aggregate tool can produce.

corr_id collapses (83–91% unattributed) across *all* sizes — the timing limit is structural, not a
tuning artifact.

---

## 2b. Mechanism decomposition — the two observer failures have *different* causes (batch sweep)

`tools/run_nixl_batchsweep.sh` varies NIXL's GDS batch size at fixed KV size (2560 B, PP8) and isolates
*why* each standard observer fails:

| batch | cuFileBatchIOSubmit (API ops) | device reads | **API coarsening** | **B A_byte (ours)** | corr_id correct | corr_id unattr |
|--:|--:|--:|--:|--:|--:|--:|
| 1 | 2200 | 2206 | **1×** | 3.200 | 0% | **100%** |
| 8 | 275 | 2206 | 8× | 3.200 | 3% | 97% |
| 32 | 69 | 2206 | 32× | 3.200 | 6% | 93% |
| 64 | 35 | 2206 | 63× | 3.200 | 9% | 90% |
| 128 | 18 | 2207 | **123×** | 3.200 | 8% | 91% |

Three things fall out, and together they are the necessity argument:
1. **cuFile-API coarsening is batch-driven** — the API-to-device op ratio tracks batch size exactly
   (1×→123×). A per-call API profiler (`cuFileGetStats`/Nsight) is only as fine as the batch.
2. **corr_id collapse is async-driven, *not* batch-driven** — it is ~90–100% unattributed at *every*
   batch size, **including batch=1, where the API is 1:1 with the device** (2200 submits, 2206 reads).
   Even one read per submit cannot be timing-attributed, because the GDS batch submit is asynchronous:
   it returns (and the per-thread correlation window closes) before the NVMe command is issued. Batching
   is not what breaks timing — *asynchrony* is; batching only additionally breaks the API layer.
3. **Address-based attribution is invariant** — class-B A_byte is exactly 3.200× at every batch size.

**Why this clinches necessity (not convenience).** The two failure axes are independent and you cannot
tune your way out: drive batch=1 to make the API fine-grained, and timing still fails (async, 100%
unattributed); raise the batch for throughput (the production default — NIXL uses 64–128), and the API
also goes blind (63–123× coarse). Only per-NVMe-command physical-address attribution is orthogonal to
*both* the API surface and the submit/issue timing, so it is the only observer that survives the real
engine's async-batched regime.

---

## 3. Robustness
- **Per-class LBA is deterministic** — A=1.000×, B=3.200× identical across 3 repeats (closed-form;
  same offsets → same device rounding).
- **corr_id collapse is consistent** — 3 repeats at PP8: 10/11/14% correct, 89/88/85% unattributed.
- **Synthetic↔real agreement** — these real-engine numbers match the synthetic NIXL-sized replay
  (Result 3) exactly (aggregate 1.052×, class-B 3.20×), validating both.

## 4. Honest scope
- Single-agent NIXL transfer with a NIXL-derived access pattern (real engine, real backend, real KV
  sizes) — not a captured multi-node prefill→decode `kvbench` trace (distributed/etcd/torch; future
  work). The transfer *engine* and its GDS path are real; the access pattern is one we drive.
- `cupy` used only to allocate/register VRAM (its compute kernels JIT-fail on this driver — irrelevant
  to the GDS path).
- nvidia-fs `Ops: BatchIO` counter stays 0 even with stats on; the GDS reads register under
  `Reads n=`, so the aggregate total (215 MiB) is captured fairly — the point is granularity, not a
  disabled counter.
