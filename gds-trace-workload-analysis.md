# GDS-Trace — Real GDS workloads + benchmarks, and the per-op gap they reveal

Answers to: is `nixlbench` like `gdsio`? could you get per-function bandwidth from `gds_stats`
instead of hand-timing? are ESPN/Tutti/TeraIO GDS workloads, and what are they? Grounded in the actual
code (`external/ESPN-v1`, added as a submodule) and Muradli et al.'s paper (same lab, same testbed).

## `gdsio` vs `nixlbench` — both stress GDS, at different layers
- **`gdsio`** (NVIDIA, v0.4.x) — a **GDS/cuFile microbenchmark**: drives the GDS path *directly*
  (cuFile→nvidia-fs→NVMe). Knobs: IO size, threads, IO depth, GPU, and **transfer type** — `CPU_GPU`,
  `GPU_DIRECT`, `GPU_DIRECT_ASYNC`, `GPU_BATCH`, `GPU_BATCH_STREAM`. GDS-specific, synthetic load.
- **`nixlbench`** ([ai-dynamo/nixl](https://github.com/ai-dynamo/nixl/tree/main/benchmark/nixlbench)) —
  benchmark for **NIXL** (NVIDIA Inference Xfer Library), a higher-level point-to-point transfer
  abstraction for **inference KV-cache movement** (used by Dynamo/SGLang/vLLM). It has **multiple
  backends** (UCX/RDMA, GPU-initiated net, **GDS as one plugin**); sweeps block/batch sizes; reports
  bandwidth + **latency percentiles** across backends. So it stresses GDS only *via the NIXL plugin*,
  and sits **above** cuFile.
- **Net:** `gdsio` = GDS-direct; `nixlbench` = GDS-as-a-backend (broader, inference-focused). Muradli
  used **both**.

## Could `gds_stats` give per-function bandwidth instead of hand-timing? — No
`gds_stats` is **per-process / per-GPU aggregate** cuFile counters (+ an IO-size histogram). It cannot
slice by **function / call-site / phase / transfer-type / backend**, reports **no userspace latency**
(driver-submission→completion only) and **no per-op records/timestamps**, and only sees the **cuFile**
path (NIXL's non-GDS backends are invisible to it). So to compare transfer types / IO sizes / backends
— and to explain *why* — you **must** instrument yourself. Confirmed in two independent places:
- **ESPN** (`external/ESPN-v1`): measures with **hand-rolled `cudaEvent` timers** + manual
  `read_bandwidth = data_size/(cpu_time_used*GB)` (`src/gds_batch/utils.cpp`, `cufile_bread.cc`), and a
  gdsio-style harness — **not** `gds_stats`. Reads **4 KiB random embedding vectors** via the cuFile
  **batch** API (`output.txt`: `XferType: GPU_BATCH ... IOSize: 4(KiB) ... 1.73 GiB/sec`).
- **Muradli** (per the paper / your note): hand-timed functions for the NIXL/GDS comparison.

**But you're right that "better tooling is missing" is a *weak* motivation by itself** — it's a
convenience/methodology gap. It becomes **research-worthy** only because the missing tooling is the
*only* way to see **cross-layer per-op pathologies** that aggregate numbers hide (next section).

## The strong, GDS-INTRINSIC motivation: per-op I/O amplification (from Muradli, same testbed)
Muradli et al., *"Insights into GPUDirect Data Transfer through NIXL Benchmarking"* (IIT/SCS —
Kougkas/Sun; Chameleon A100 + ext4, identical to ours) reports **GDS-internal** effects, not reader
policy:
1. **I/O amplification:** "the GPU page size being 64 KB. When requests ≥ 128 KB do not align
   perfectly in memory, it can cause **several additional P2P DMA requests** … which increases the
   number of small I/O operations to the NVMe compared to CPU\_GPU" → **GDS ends up slower than the CPU
   path** for large unaligned writes.
2. **Batching is fake:** "GDS does **not truly implement batching**, and submits each request in the
   batch individually."
3. **GDS isn't always faster:** "sensitive … kernel overheads, page sizes, PCIe topology, NVMe, NUMA."

These are **exactly cross-layer, per-op** (one cuFile op → N P2P-DMA/NVMe requests) — invisible to
`gds_stats` (aggregate), `nvidia-smi` (PCIe only), Darshan (no cuFile), and even gdsio (reports the
*symptom* — low BW — not the *amplification factor* per op). **We already glimpsed it:** our unaligned
`.npy` did **468 kernel reads for ~300 logical reads** (1.56×). This is GDS-intrinsic and is the
cleanest motivation: *"for this cuFile op, how many NVMe requests did it become, and why (alignment /
page / threshold / topology)?"* — the per-op cross-layer attribution GDS-Trace would provide.

## The workloads (all small/random SSD→GPU — where this bites)
| workload | GDS role | use case | code |
|---|---|---|---|
| **ESPN** (ISMM'24, arXiv 2312.05417) | **uses GDS** (cuFile batch) | memory-efficient **multi-vector embedding retrieval / RAG**: offload embedding tables to SSD, read 4 KiB vectors SSD→GPU; 5–16× memory cut + prefetcher | **public → `external/ESPN-v1`** |
| **TeraIO** (NeurIPS'25, arXiv 2506.06472) | **uses GDS** | **cost-efficient LLM training**: lifetime-aware tensor offload to PCIe SSDs; 1.47× over ZeRO-Offload/Infinity | no public repo found |
| **Tutti** (arXiv 2605.03375) | **GDS-adjacent / critique** | **SSD-backed KV-cache for long-context LLM serving**: argues GDS stays CPU-centric for tiny random I/O (fragmented KV → massive tiny I/Os → GPU stalls **even with GDS**); builds a GPU-native path | no public repo found |

Common thread: **tiny/random SSD→GPU reads** (embeddings, KV-cache, tensors) — the regime where
amplification, the 16 KiB reader threshold, async/batch overheads, and path decisions all collide, and
where per-op cross-layer attribution matters most. Muradli (NIXL/KV-cache transfer) is a **local,
reachable design partner / potential co-author** working this exact problem on the same testbed.

## Proposed next experiment (quantify the GDS-intrinsic amplification)
Sweep cuFile read/write **size × alignment** and measure **kernel NVMe requests per logical op**
(`/proc/driver/nvidia-fs/stats` Reads count vs logical count, and `iostat` device IOPS) to produce the
**amplification factor** curve — reproducing Muradli's 64 KB-page effect and turning "468 vs 300" into
a clean per-op result. That, plus the reader-threshold divergence, is the motivating evidence set.
