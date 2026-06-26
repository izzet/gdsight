# From attribution to optimization (O1/O2) — does better observation *drive* a fix, and would existing tools?

**Question.** Observability only matters if it drives a fix. Does our per-op cross-layer attribution
lead to a *measured* optimization that existing tools would **not** have driven — ideally where every
shipping tool reports the system **healthy** while ours sees the misconfiguration?

**Setup.** One KV-streaming workload on the **real NIXL `cuda_gds` engine** (120,000 × 2560 B
Llama-3.1-70B PP8 KV reads + 1 large baseline, true P2P file→VRAM, cold cache), run under three
KV-cache **layouts**. Reproducible: `tools/run_nixl_opt.sh`, `workloads/nixl_gds_kv.py --layout`.
Throughput is measured **untraced** (production win); the per-op attribution (device bytes, command
fan-out, A_byte) comes from the traced run.

| layout | what it models | device bytes (ours) | A_byte (ours) | NVMe cmds (ours) | cuFile-API ops | **throughput** |
|---|---|--:|--:|--:|--:|--:|
| **scatter** | naive paged KV, sub-4K-misaligned (status quo) | 938 MiB | **3.2×** | **120,009** | 1876 | **0.196 GiB/s** |
| **aligned** | the "obvious" fix: 4K-align each read | 469 MiB | 1.6× | 120,012 | 1876 | 0.177 GiB/s |
| **packed** | the fix our attribution points to: pack contiguous + bulk/coalesce | 293 MiB | **1.0×** | **301** | 5 | **2.692 GiB/s** |

---

## O1 — the fix our attribution drives: **13.7× throughput**
scatter → packed: **0.196 → 2.692 GiB/s (13.7×)**, device bandwidth **3.2× lower** (938→293 MiB),
and **400× fewer NVMe commands** (120,009→301). The workload moves from **op-rate-bound** (0.2 GiB/s,
far below the drive) to **bandwidth-bound** (2.69 GiB/s ≈ the single-drive ceiling). The fix is a
KV-cache **layout** change (scatter→packed+coalesced), and it is exactly what our per-class device
attribution identifies as the waste.

## Why **no existing tool would have driven this fix** (they all report "healthy")
For the naive `scatter` layout, every standard observer is **green**:
- **nvidia-fs `/proc` stats (gds_stats-class):** `err=0`, **all reads on GDS** (no POSIX fallback),
  938 MiB read — i.e. "GDS is working, no errors." ✅ healthy
- **iostat / diskstats:** 938 MiB read, drive busy — "the drive is doing work." ✅ healthy
- **cuFile API (Nsight/cuFileGetStats):** 1876 batch ops, no errors. ✅ healthy

None of them carries the *application-requested* bytes per op, so none can say the drive is moving
**3.2× more data than asked** and drowning in **120k commands for 293 MiB of useful data**. An operator
watching these dashboards sees a busy, error-free GDS pipeline and **has no reason to change anything**.
Only our per-op cross-layer attribution surfaces `B_A_byte=3.2×` + the 120k-command fan-out → the fix.

## O2 — the fix that *partial* visibility suggests is **wrong** (the align trap)
If you only knew the reads were *misaligned* (a single-dimension hint), the obvious fix is 4K-alignment.
It **halves the device bandwidth waste** (938→469 MiB, A_byte 3.2×→1.6×) — a metric that *looks like
progress* — yet throughput **does not improve** (0.196→0.177 GiB/s, in fact slightly worse). Reason:
aligning reduces *bytes per op* but leaves **120,012 ops**, and the workload is **command-rate-bound**,
not bandwidth-bound. The right lever is **coalescing** (collapse 120k ops → 301), which requires seeing
**both** the per-op device bytes **and** the command fan-out at once — a view only per-op cross-layer
attribution provides. Batching more (the cuFile-API-visible lever) also does nothing: the batch-size
sweep showed device amplification is **invariant at 3.2× across batch 1→128**.

So the two fixes a partial/aggregate view would suggest — *align* and *batch more* — **both fail**;
only the layout/coalesce fix our attribution points to delivers the 13.7×. (Compare the command-amp
*null* in `interference.md`: we don't claim a lever until it's measured.)

## Why a *formula* can't substitute for the *tool*
Computing the cost needs two inputs, **both unknowable a priori**:
1. **The per-op access geometry** is produced *inside* the engine (NIXL batching, KV-cache layout,
   model sharding) — not visible to an operator, and it changes with config.
2. **The amplification granularity G is a stack property, not the device spec.** On this node the
   **device logical block is 512 B**, but the measured granularity is **4096 B** (ext4 block +
   O_DIRECT page alignment). Change the FS/mount/LBA-format/`max_sectors_kb` and G changes. You cannot
   read G off a datasheet — it only appears in the **actual NVMe traffic** our tracer observes.

And even *with* the formula you would still pick the **wrong lever** (align, per O2) unless you also see
the command fan-out. The optimization is therefore driven by **observation**, not calculation.

## Honest notes
- Single drive; the 13.7× is for a **KV-streaming-shaped** workload where the per-class waste dominates.
  On a *mixed* job (the §5.1b keystone) the same fix is ~5% of total — which is exactly why aggregate
  tools ignore it and ours doesn't: per-class attribution tells you **which workloads** the lever pays
  off on.
- Throughput measured untraced (the production win); attribution from the traced run. Smaller KV
  (256 B layer-access, 16×) amplifies more; 2560 B (PP8) is the representative keystone size.
