# Curated GDS anti-patterns: where standard tools lead to the WRONG decision

Three realistic, hand-curated GPUDirect Storage anti-patterns where the **standard-tool-driven conclusion
is wrong** and per-op cross-layer attribution (GDSight) gives the right fix. Each models a *documented*
real anti-pattern (cited), but **these are motivating examples — curated to expose the tool gap — not
evidence of demand.** We constructed them; proving real-world prevalence needs a production deployment.
(We first ran battle-tested workloads — cuDF, elbencho — and they were *clean*; pathologies live in
naive/misconfigured usage, which is what these cases model.)

Setup: A100, CUDA 12.6, nvidia-fs 2.28, single PM983 NVMe (ext4 `data=ordered`). GDSight = DataCrumbs
(eBPF: cuFile uprobe + nvidia-fs/NVMe kprobes + per-op `corr_id`) → DFAnalyzer. Reproduce:
`tools/run_curated_suite.sh`.

## Case 1 — the "buy faster storage" trap (unaligned data layout)
**Anti-pattern:** data laid out without 4 KiB alignment — e.g. 768-dim fp32 embedding rows = 3072 B;
DLRM-on-SSD documents "only 512 B of each 4 KB block is relevant" ([arXiv:2110.11489](https://arxiv.org/pdf/2110.11489)).
At serving concurrency (4 KiB random GDS reads, `-w 64`):

| tool | what it reports | decision it leads to |
|---|---|---|
| `iostat` / `gds_stats` | device busy, useful throughput **0.46 GiB/s** | ❌ *"we're storage-bound — buy a faster drive"* |
| **GDSight** | **byte-amp = 2.0×** (device reads 512 MiB for 256 MiB requested) | ✅ **"unaligned reads waste half the bandwidth — align the data, ~2× for free"** |

Measured: aligned **1.15 GiB/s** vs unaligned **0.46 GiB/s** (2.5×) at `-w 64`; device moves 2× the bytes.

## Case 2 — the "GDS is healthy" trap (silent sub-threshold POSIX)
**Anti-pattern:** bimodal reads (small metadata/features + large payloads) through kvikio, whose default
`KVIKIO_GDS_THRESHOLD` (16 KiB) silently routes sub-16 KiB reads to POSIX *above* cuFile. 4000 reads, 60% small:

| tool | what it reports | decision |
|---|---|---|
| `gds_stats` / nvidia-fs | **1617 GDS reads, 101 MiB, GDS ok** | ❌ *"GDS is working"* (blind to the 60% on POSIX) |
| **GDSight** | **1617 `cuFileRead` (GDS) + 2385 `pread64` (silent POSIX)** | ✅ **"60% of reads (<16 KiB) bypass GDS — batch/coalesce them or lower the threshold"** |

Measured: nvidia-fs `n` += exactly 1617 (the large reads); the 2385 small reads are invisible to gds_stats,
visible per-op as `pread64` in ours.

## Case 3 — the "which tensor to optimize" trap (heterogeneous multi-feature)
**Anti-pattern:** multi-feature/multi-model gather mixing aligned + unaligned tensors (768-dim vs
1024-dim; ESPN's CLS+BOW, [arXiv:2312.05417](https://arxiv.org/html/2312.05417v1) — whose authors
hand-fixed exactly this). 4000 interleaved reads from two tables:

| tool | what it reports | decision |
|---|---|---|
| `gds_stats` / `iostat` | **1.36× aggregate byte-amp** | ❌ *"some waste somewhere"* (can't localize) |
| **GDSight** | **table B (3072 B) = 2.015×, table A (4096 B) = 1.000×** | ✅ **"table B (768-dim) wastes 2× — fix its layout; A is fine"** |

## Closing the loop: tool-guided fixes → measured wins
The per-op diagnosis names a *specific* fix in each case; applying it and measuring closes the loop —
going **beyond the stop-at-characterization norm** of cross-layer tracers (Recorder, DFTracer, zns-tools;
see `related-work.md`). Each fix is exactly what the per-op output points to (no other tool gives it):

| case | tool-named fix | before | after | win |
|---|---|---|---|---|
| **1** unaligned layout | align data to 4 KiB | 0.50 GiB/s | 1.15 GiB/s | **2.3× throughput** |
| **2** sub-16 KiB silent POSIX | coalesce reads to ≥16 KiB (→ GDS) | 29 MiB/s (POSIX, 0 GDS ops) | 149 MiB/s (4096 GDS ops) | **5.1× throughput** |
| **3** unaligned 768-dim rows | pad rows to 4 KiB slots | 6029 B/row device | 4194 B/row device | **1.44× less device BW/row** |

Honest notes: Case 2's win also reflects fewer/larger ops from coalescing (assumes the small items are
batchable/contiguous); Case 3's padding trades unaligned 2× for a padded ~1.37× (1.44× device-BW
reduction, ~1.4× throughput at saturation), not a perfect 1×.

## The pattern + honest scope
In all three, standard tools report an aggregate that is **incomplete or actively misleading** → the wrong
action (*buy hardware / "it's fine" / optimize blindly*); per-op cross-layer attribution → the **specific,
correct fix**. The enabler is per-op `corr_id` correlation **below cuFile** — no shipped tool spans
cuFile↔nvidia-fs↔NVMe per op (`gds_stats` is cuFile-aggregate; `iostat`/nvidia-fs are device-aggregate;
`iotop` is per-process; Nsight stops at the cuFile API).

We then **close the loop**: the per-op output names the fix, we apply it, and measure the win (2.3× / 5.1×
/ 1.44×) — which is what lifts this above the stop-at-characterization tracers.

**Honest limits:** (1) these are curated on a *robust modern stack* — they prove the **capability**, the
**gap**, and that the **tool-named fix works**, but not that production deployments hit them at scale;
(2) each effect is individually known to experts — the contribution is *automatic, per-op attribution that
names the fix without foreknowledge, plus the measured fix*; (3) demand (a real team blocked by one of
these, undiagnosable today) still needs a design partner to confirm.
