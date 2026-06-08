# GDS-Trace tracer — validated end-to-end results + cross-checks

The working tracer: **DataCrumbs** (eBPF; cuFile uprobe plugin sync+async+batch with per-op
size/offset/count, NVMe `nvme_setup_cmd` kprobe with size/sector, TGID worker-thread fix) →
**DFTracer `.pfw.gz`** → **DFAnalyzer** (datacrumbs/stack preset; nests `nvme_setup_cmd` under the
`cuFileRead` that issued it) → per-op cross-layer attribution + amplification. Driver:
`tools/dfa_drive.py`. Build/how-to: `DATACRUMBS-GDS-BUILD-LOG.md`.

## End-to-end results (real + synthetic readers through the full stack)
| reader / workload | cuFile API | cuFile ops | NVMe cmds | **device-cmd amplification** | bytes (cuFile = NVMe) | notes |
|---|---|---:|---:|---|---|---|
| **gdsio** `-i 1M` aligned | cuFileRead | 256 | 256 | **1.0×** | 256 = 256 MiB | baseline (≤MDTS) |
| **gdsio** `-i 4M` | cuFileRead | 64 | 256–375* | **~4–6×** | 256 = 256 MiB | 4 MiB reads split at ~1.25 MB MDTS |
| **gdsio** `-x 5` async | cuFileReadAsync | 128 | — | — | size=1 MiB via `size_t*` deref | async arg capture |
| **gdsio** `-x 6` batch | cuFileBatchIOSubmit | 125 | 128 | — | `count`+`size` from `CUfileIOParams_t` | batch arg-walk |
| **kvikio** (`step3_ragged`) | cuFileRead (sync, dlsym'd, worker-thread) | 3000 | 3013 | **1.0×** | 1601 = 1601 MiB | ragged ~546 KiB < MDTS → 1 cmd each |
| **DALI** numpy GPU reader | cuFileRead (sync) | 9137 | 22568 | **2.47×** | 9875 ≈ 9896 MiB | whole-file ~1–1.8 MB > MDTS → 2–3 cmds; **re-registers a handle per read**; also 13k `pread64` (POSIX header reads), 300 of which hit NVMe |
| **ESPN** `cufile_bread` (batch) | cuFileBatchIOSubmit | 32 submits (128 sub-ops each) | 4096 | 128 cmds/submit (1:1 per sub-op, 4 KiB) | 16 = 16 MiB | batch arg-walk gives `count=128,size=512KiB`/submit; all 4096 NVMe → root cuFileBatchIOSubmit; **4096 `cuFileHandleRegister` (0.115 s) > batch reads (0.053 s)** |

\* run-to-run variance (see drift notes).

**Amplification is a size-dependent *device* effect, not reader-specific** (NVMe MDTS ~1.25 MiB splits
larger reads): kvikio ragged (~546 KiB, <MDTS) = **1.0×**, but kvikio 4 MiB = **4.05×** (300 cuFileRead
→ 1214 NVMe), DALI whole-file = 2.47×, gdsio -i4M = ~4×. GDS-Trace surfaces it per-op for any reader.

**What no NVIDIA tool shows here:** per-op `cuFileRead{size,offset}` → the exact set of NVMe commands
it became (with sizes), the device-command amplification, kvikio's dlsym'd/worker-thread reads, and
DALI's per-read handle-registration + GDS/POSIX mix. `gds_stats` is aggregate cuFile-only; NVTX stops
at cuFile; Darshan has no cuFile.

## Third layer: nvidia-fs (the middle) — true P2P vs silent bounce, per op
The brief's pitch is **cuFile ↔ nvidia-fs ↔ NVMe**; the middle layer is now instrumented (custom
`nvidiafs` plugin, `event_type 6`, kprobes on `nvidia-fs.ko`):
- **`nvfs_io_start_op`** — per-op driver entry, **1 per `cuFileRead`** → the cuFile↔nvidia-fs bridge.
- **`nvfs_get_p2p_dma_mapping`** — the op took the **true zero-copy P2P DMA** path (real GDS).
- **`nvfs_mgroup_pin_shadow_pages`** — the op **staged through a host shadow/bounce buffer** (not
  zero-copy) — i.e. "GDS configured but silently bouncing", the core correctness pathology.

So GDS-Trace now gives a **per-op verdict: was this read actually zero-copy, or bounced?** Validated
gdsio `-i1M`: 256 cuFileRead → 256 `nvfs_io_start_op` → 320 `nvfs_get_p2p_dma_mapping`, only 4
`pin_shadow_pages` → **true P2P**. **Correction to an earlier inference:** kvikio with `BufRegister=0`
also does true P2P (`p2p_mapping`=3003, `pin_shadow`=1 over 2000 reads) — *no pre-registration ≠
bounce*; the driver maps GPU pages P2P per op regardless. The nvidia-fs layer is what settles this per
op (we previously could only guess from the aggregate `BufRegister`/`Active Shadow-Buffer` counters).

## Bounce/compat induction — caught the per-op fallback + its cost (cross-layer timing)
Deliberately induced a fallback by reading from a **non-GDS mount** (OS root ext4, *not* `data=ordered`)
so cuFile silently drops to **compat/POSIX**, then compared to a true-GDS read of the same size. gdsio
*reports `XferType: GPUD` either way* (claims GDS!), but the stack tells the truth:

| | **GDS** (`/mnt/nvme1`, `data=ordered`) | **COMPAT** (root ext4 fallback) |
|---|---|---|
| `cuFileRead` ops | 256 | 256 (gdsio still says `GPUD`) |
| `nvfs_io` (nvidia-fs) | **256** | **0** ← the verdict |
| data path (child) | `nvfs_io` → P2P → NVMe | **`pread64`** (POSIX), no NVMe in-process |
| kernel `nvidia-fs Reads` Δ | **+256 / +256 MiB** | **0 / 0 MiB** (oracle blind) |
| cuFileRead wall-time | **0.338 s** | **1.919 s (5.7× slower)** |

**Cross-layer timing — done in DFAnalyzer** (`compute_self_time` → per-event `self_time`/`child_time`;
overlap via `get_job_time`), surfaced by `tools/dfa_drive.py`. *No custom trace parser.*
- **GDS:** per `cuFileRead`, **self_time (cuFile/userspace) = 0.9%**, **child_time (nvidia-fs+device via
  `nvfs_io`) = 99.1%** → the cuFile API adds ~0 overhead; cost is the real I/O.
- **COMPAT:** `cuFileRead` child_time is **`pread64` (98.2%)** with **zero `nvfs_io`** — the fallback,
  per op, and **5.7× slower** (532 MB/s vs 2.9 GB/s).
- **Overlap:** both fully pipeline (overlap factor **3.97×** over 4 workers, zero idle gaps) — so the
  compat penalty is **per-op path cost, not lost concurrency** (a distinction the timing view makes).

This needs `nvfs_io` as a **duration** op (`nvfs_io_start_op`→`nvfs_io_complete`, same thread) so
DFAnalyzer's hierarchy attributes time across cuFile→nvidia-fs→device. Honest note: `gds_stats` has an
aggregate `posix` counter that *can* flag compat in bulk; GDS-Trace adds the **per-op** verdict, the
**5.7× latency cost**, and the fact the kernel GDS counter shows **nothing** while 256 MiB moved.

## Real workload: vLLM's fastsafetensors GDS weight loader
First real (non-synthetic, in-production) GDS workload through the stack: `--load-format fastsafetensors`
(vLLM's GPUDirect weight loader, IBM foundation-model-stack), loading a 2 GiB safetensors shard from
`/mnt/nvme1`. Script: `workloads/fastsafetensors_load.py` (GDS vs `--nogds` fallback).

| | **GDS** (`nogds=False`) | **nogds** (CPU-staged fallback) |
|---|---|---|
| cuFile API | **1 `cuFileRead` + 1 `cuFileHandleRegister`** | none |
| data path | 1 read → 131 `nvfs_io` → 1786 NVMe (true P2P) | **2050 `pread64`** → CPU → 2205 NVMe |
| throughput | **2.65 GiB/s** | 2.12 GiB/s (~25% slower) |
| cross-layer timing | cuFileRead: **2.7% cuFile/userspace, 97.3% below** | POSIX `pread64` self-time |

Findings: fastsafetensors is **well-behaved** — it registers the handle **once** and issues **one giant
`cuFileRead`** for the whole file (cross-checked: independent bpftrace = 1 `cuFileRead`; kernel oracle =
+2048 MiB GDS), the *opposite* of DALI/ESPN's per-read churn. That 1 user read → **1786 device commands**
(2 GiB split at MDTS). GDS's edge over the fallback is modest here (single drive, host-BW bound) but the
I/O *shape* differs completely (1 zero-copy read vs 2050 staged POSIX reads).

**Two real tracer findings this workload surfaced (matters for any torch-based GDS app):**
1. **torch bundles its own `libcufile`** (pip `nvidia-cufile-cu12` wheel → `site-packages/nvidia/cufile/
   lib/libcufile.so.0`), so a uprobe hardcoded to the *system* CUDA `libcufile` misses the user layer
   (kernel `nvfs_io`/NVMe still fire). Fix: `LD_PRELOAD` the traced `libcufile` (or point the uprobe at
   the loaded one). The tracer should attach to the *actually-loaded* `libcufile`.
2. **cuFile uses internal worker threads** — and we fixed the attribution for it. fastsafetensors makes
   1 `cuFileRead` on its thread, but cuFile dispatches the 16 MB chunks across its own worker-thread pool,
   so the `nvfs_io`/NVMe land **off the caller thread**. Per-tid time-containment therefore **split** the
   attribution (only 465/1817 NVMe → `cuFileRead`, the rest stranded on worker threads = 25.6%).
   **Fix — correlation id through the layers** (all in our plugins, no DataCrumbs core changes): the cuFile
   plugin tags each op with a `corr_id` (= entry ts) in a shared map; `nvfs_io`/NVMe stamp the active
   op's `corr_id` (same-thread lookup, with a per-process fallback used only when exactly one cuFile op
   is in flight — unambiguous). DFAnalyzer attributes by `corr_id`, not time. Result: **1815/1817 NVMe →
   the cuFileRead = 99.9%** (vs 25.6% by time-containment), and gdsio's synchronous case is unchanged
   (98.5% either way — no regression). Trade-off: to keep attribution clean, `nvfs_io` is now a **point**
   event (its async start→complete duration was unreliable), so per-layer *time* decomposition is no
   longer reported (cuFileRead latency still is); recovering it needs correct per-layer async durations.

**Real HF model (Qwen2.5-3B-Instruct, 2 safetensors shards, 5.75 GiB, 434 tensors).** Loaded via
`fastsafetensors_load.py --path <model_dir>` (vLLM-style, all shards). GDS-Trace: **2 `cuFileRead` +
2 `cuFileHandleRegister`** (register-once per shard) → **5168 NVMe** commands, true P2P (5159
`p2p_dma_mapping`, 0 shadow), **2584× device-cmd amplification**, 5886 MiB cuFile == 5886 MiB NVMe
(bytes conserved), 2.78 GiB/s. Confirms the tool on a genuine model, not a synthetic shard.

*Limitation surfaced (concurrent multi-shard).* fastsafetensors loads the 2 shards **concurrently**
(2 overlapping `cuFileRead`s sharing one worker pool). corr_id is robust when ≤1 op is in flight
(single shard 99.9%; gdsio synchronous 98.5%) but drops to **~36%** here: with >1 cuFileRead active,
the per-process fallback correctly refuses to guess which read a worker-thread op serves, so worker-pool
NVMe (the bulk) are left unattributed rather than misattributed. Process-level results (total
amplification, bytes, P2P) are still exact; only *which of the concurrent reads* a worker op belongs to
is ambiguous. A robust fix needs a per-request key (e.g. the GPU buffer vaddr from
`nvfs_get_p2p_dma_mapping`), which requires reading a non-BTF module struct at a hardcoded offset
(version-brittle) — deferred.

## Inference-time KV-cache offload: LMCache's GDS path (writes + reads)
The other live GDS use case in LLM serving is **KV-cache offload** (spill KV beyond HBM to NVMe, reload
on hit). Traced via `workloads/lmcache_gds_kv.py`, which drives **LMCache's GDS mechanism through
cufile-python** — the exact module + calls (`cufile.CuFile(...).write/.read`) LMCache's `GdsBackend`
uses in `_save_gds`/`_load_gds` (4 KiB POSIX metadata header + GPUDirect write at offset 4096; GDS read
back). *The full `lmcache.v1.…GdsBackend` can't run here — lmcache 0.4.6 ships a CUDA-13 `c_ops`
extension (`libcudart.so.13`) that won't load on this CUDA-12.6 node; cufile-python is independent of it.*

8×32 MiB KV chunks, offload then reload: **8 `cuFileWrite` + 8 `cuFileRead` + 16 `cuFileHandleRegister`
→ 512 `nvfs_io` → 514 NVMe**, true P2P. **corr_id attribution = 100% (514/514 NVMe → a cuFile op)**,
**32× device-cmd amplification** (32 MiB chunk > MDTS), 512 MiB cuFile == 512 MiB NVMe (conserved).
GDS writes are stable here (controller healthy throughout) — the historical controller drop was ACS, not
GDS writes per se (`amd_iommu=off`, ACS intact).

Findings: (1) **first GDS *write* workload traced** — the `cuFileWrite` offload path, attributed
per-op like reads. (2) LMCache opens a **CuFile per chunk per op** → 16 `cuFileHandleRegister` for 16
ops (the same register-once-not-per-op inefficiency seen in DALI/ESPN). (3) This driver is sequential so
corr_id is 100%; LMCache's real backend uses a `gds_io_threads` pool (concurrent reads) and would hit
the same concurrent-attribution limit documented above for multi-shard fastsafetensors.

## The overhead that matters: byte amplification vs read size (not command amplification)
Two distinct "amplifications", and only one is a real cost:
- **Device-command amplification** (the 4× / 32× / 2584× headlines): **bytes are conserved** — it's just
  MDTS (~1.25 MiB) splitting one cuFile op into N NVMe commands. Total commands ≈ `total_bytes/MDTS`
  *regardless of how you chunk into cuFile ops*, so per-op amplification is largely an accounting ratio
  (a high value = efficient big reads), and on this BW-bound drive throughput stays at the ~2.9 GiB/s
  ceiling. Not a cost here.
- **Byte amplification**: the device moves **more bytes than requested** = genuine wasted device/PCIe
  bandwidth. Measured (corr_id-clean: only the NVMe attributed to each `cuFileRead`), gdsio `-U` randread:

  | unaligned read | device bytes / requested |
  |---:|---|
  | **4 KiB** | **2.00×** (exact: 8 MiB req → 16 MiB device) |
  | 16 KiB | 1.27× |
  | 64 KiB | 1.07× |
  | 256 KiB | 1.02× |
  | 1 MiB | 1.00× |
  | 4 KiB **aligned** | **1.00×** (exact) |

  It follows `≈ 1 + 4KiB_block_overhead / read_size`: small unaligned reads pay the full block-alignment
  tax (a 4 KiB read straddling a 4 KiB boundary pulls 8 KiB), large reads amortize it away. It is
  **invisible to `gds_stats`/throughput** (all still report clean GDS), and it bites precisely in the
  **small-read regime GDS is sold for** — KV-cache chunks, embedding/ESPN gathers. The corr_id work makes
  this *exact* per op (2.000×, not "~2 ± metadata-NVMe noise").

**In situ on a real workload — embedding gather** (`workloads/embedding_gather.py`, ESPN/DLRM/retrieval
style: 4000 random fixed-size rows from a 4 GiB table → GPU via cuFile, table opened once + one reused
buffer). The common **768-dim fp32** layout = **3072 B/row** (not 4 KiB-aligned):

| row layout | byte-amp (corr_id-clean, 100% attributed) | path |
|---|---|---|
| **768-dim fp32 (3072 B, unaligned)** | **2.000×** (11.72 MiB req → 23.44 MiB device) | 4000 cuFileRead, **1** handle-reg, true P2P (shadow≈0) |
| 1024-dim fp32 (4096 B, aligned) | 1.000× (15.62 = 15.62 MiB) | same, no waste |

So a realistic retrieval/embedding gather **silently reads 2× the bytes off NVMe** — exactly `2.000×`
because 3072 = ¾·4096 makes half the rows straddle a block. It's **register-once + true P2P** (not
bounced), so this is *pure alignment waste at the device*, not handle churn or staging; and it's
**invisible to `gds_stats`/throughput** (both report clean GDS at the same bandwidth). The fix is
actionable — pad rows to a 4 KiB multiple (or coalesce gathers) — and GDS-Trace is what makes the waste
visible and exact, per op, in the small-read regime GDS is actually deployed in.

(The other axis — host **CPU** spent building/reaping the N device commands in the `nvfs_io`/
`nvme_setup_cmd` submission path — is the regime where *command* amplification would cost; on this
BW-bound drive it's negligible, but it would dominate on faster/IOPS-bound storage.)

## Cross-check vs ground-truth tools (the rigor) — gdsio `-i 4M -s 256M -x 0`
**Same run**, three independent measurements:
| layer | GDS-Trace | kernel `/proc/driver/nvidia-fs/stats` | bpftrace (independent kprobe) |
|---|---:|---:|---:|
| cuFile reads | cuFileRead = **64** | Reads `n` = **64** | — |
| cuFile bytes | **256 MiB** | readMiB = **256** | — |
| NVMe commands | nvme_setup_cmd = **259** | — | nvme_setup_cmd = **259** |

→ **cuFile layer exact** (64 = 64, 256 MiB = 256 MiB); **NVMe layer exact, same-run** (259 = 259). The
tool's per-op capture matches the kernel oracle and an independent kprobe with **zero drift in a single
run**. kvikio cross-checked the same way: cuFileRead 3000 ≈ kernel Reads `n`, bytes 1601 MiB ≈ readMiB.

## Pathologies GDS-Trace diagnoses that coarse tools miss
Each is a per-op cross-layer effect invisible to `gds_stats` (aggregate, cuFile-only), `nvidia-smi`
(PCIe only), throughput, or Darshan (no cuFile) — but GDS-Trace pinpoints it per op.

1. **kvikio sub-16 KiB path divergence (silent POSIX).** Bimodal kvikio workload (60% reads <16 KiB):
   GDS-Trace shows **13529 `cuFileRead` (GDS, sizes ≥64 KiB) + 20472 `pread64` (POSIX, the small
   reads kvikio routed off GDS, ~166 MiB)**. Cross-check, same class of run: **`gds_stats` reports
   `posix=0`** ("GDS perfect") and the kernel `nvidia-fs Reads n` counts **only the large reads** — i.e.
   60% of reads silently took POSIX and no GDS tool shows it; GDS-Trace shows *which* reads and how much.
2. **DALI device-command amplification.** DALI numpy GPU reader: **9137 `cuFileRead` → 22568
   `nvme_setup_cmd` = 2.47×** (whole-file ~1–1.8 MB reads split at the ~1.25 MB MDTS into 2–3 commands),
   plus a **`cuFileHandleRegister` per read** (9137 — handle-reg overhead) and a `cuFileRead`(GDS)/
   `pread64`(POSIX header) mix. `gds_stats` shows aggregate GDS bandwidth (looks fine); GDS-Trace shows
   the per-op IOPS cost + the handle churn.
3. **Unaligned byte amplification.** gdsio 64 KiB random read, aligned vs `-U` unaligned: op count
   identical, but device **bytes go 128 MiB → 136 MiB = 1.06× byte amplification** (cuFile reads the
   4 KiB-aligned superset). Still reported as `GPUD` (GDS), so `gds_stats`/throughput look clean;
   GDS-Trace shows cuFileRead requested 128 MiB while NVMe moved 136 MiB — 6% wasted device bandwidth.
4. **Redundant per-read handle registration (ESPN).** ESPN's batch reader registers a `cuFileHandle`
   **per read** (4096 `cuFileHandleRegister` for 4096 reads of one file) — GDS-Trace shows it spends
   **more wall-time registering handles (0.115 s) than on the batch reads themselves (0.053 s)**. A
   diagnosable GDS-usage inefficiency (register once, not per op) that `gds_stats` (no per-op
   HandleRegister timing) cannot surface.

**Why this is the contribution:** these are exactly the "is GDS actually doing what I think, per op?"
questions from the demand evidence (forum users `fuyao3860`/`pandeyshweta2401`; Muradli's hand-rolled
timers). GDS-Trace answers them with per-op, cross-layer ground truth that no shipped tool provides.

## Drift notes (why numbers differ where they do)
1. **NVMe-command count varies run-to-run** (256 / 259 / 375 for identical gdsio `-i4M`). Cause:
   device-side splitting of >MDTS (~1.25 MB) reads is non-deterministic (MDTS, queue state, alignment).
   So *amplification is a per-run measurement, not a fixed constant* — but within any single run the
   tool matches bpftrace exactly. Compare tool↔oracle **within the same run**, never across runs.
2. **~0.3% of `nvme_setup_cmd` fire in non-process (softirq/kworker) context** (bpftrace saw 376 total
   vs 375 in `gdsio`), which the TGID filter does not attribute — a negligible undercount, and those
   completions aren't attributable to a process op anyway.
3. **`iostat` (device-wide) ≥ GDS-Trace (process-scoped):** iostat counts all I/O incl. other tenants/
   background; GDS-Trace counts only the traced process — by design.
4. **`gds_stats` per-GPU `n` is misleading** (showed 0 while the kernel DMA'd, see STEP3-FINDINGS) —
   trust GLOBAL `Read: ok` / kernel counters; GDS-Trace matches those.
