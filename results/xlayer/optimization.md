# From attribution to optimization (O1/O2)

> **Superseded values (2026-08-06).** Measured at KV size 2560 B. The workload was later standardised
> on 2048 B, the Llama-3.1-70B per-token per-layer slice, and re-measured with repetitions. Current
> values: amplification 4.000x (not 3.2x), commands 120,008 -> 239 (not 120,009 -> 301), throughput
> 0.152+/-0.012 -> 2.602+/-0.031 GiB/s = 17.1+/-1.3x over five untraced runs (not 0.196 -> 2.692 =
> 13.7x). Sources: `optimization.csv`, `reps.csv`. This note is kept as the record of the earlier run.

**Question.** Does per-op cross-layer attribution lead to a measured optimization that existing tools
would not have driven, in a case where every shipping tool reports the system healthy?

## Setup
One KV-streaming workload on the real NIXL `cuda_gds` engine (120,000 × 2560 B Llama-3.1-70B PP8 KV
reads + 1 large baseline, true P2P file to VRAM, cold cache), run under three KV-cache layouts.
Reproduce: `tools/run_nixl_opt.sh`, `workloads/nixl_gds_kv.py --layout`. Throughput is measured
untraced; the per-op attribution (device bytes, command fan-out, A_byte) comes from the traced run.

| layout | what it models | device bytes (GDSight) | A_byte (GDSight) | NVMe cmds (GDSight) | cuFile-API ops | throughput |
|---|---|--:|--:|--:|--:|--:|
| scatter | naive paged KV, sub-4K-misaligned (status quo) | 938 MiB | 3.2× | 120,009 | 1876 | 0.196 GiB/s |
| aligned | the "obvious" fix: 4K-align each read | 469 MiB | 1.6× | 120,012 | 1876 | 0.177 GiB/s |
| packed | the fix the attribution points to: pack contiguous + bulk/coalesce | 293 MiB | 1.0× | 301 | 5 | 2.692 GiB/s |

---

## O1: the attribution-driven fix gives 13.7× throughput
From scatter to packed: 0.196 to 2.692 GiB/s (13.7×), device bandwidth 3.2× lower (938 to 293 MiB),
and 400× fewer NVMe commands (120,009 to 301). The workload moves from op-rate-bound (0.2 GiB/s, far
below the drive) to bandwidth-bound (2.69 GiB/s, about the single-drive ceiling). The fix is a KV-cache
layout change (scatter to packed and coalesced), and it is the waste that the per-class device
attribution identifies.

## What existing tools report for the scatter layout
Every standard observer reports healthy:
- nvidia-fs `/proc` stats (gds_stats class): `err=0`, all reads on GDS (no POSIX fallback), 938 MiB
  read, i.e. GDS working with no errors. ✅ healthy
- iostat / diskstats: 938 MiB read, drive busy. ✅ healthy
- cuFile API (Nsight/cuFileGetStats): 1876 batch ops, no errors. ✅ healthy

None of them carries the application-requested bytes per op, so none can show that the drive is moving
3.2× more data than requested and issuing 120k commands for 293 MiB of useful data. An operator watching
these dashboards sees a busy, error-free GDS pipeline and has no reason to change anything. Only per-op
cross-layer attribution surfaces `B_A_byte=3.2×` and the 120k-command fan-out that lead to the fix.

## O2: the fix suggested by partial visibility does not help
Knowing only that the reads are misaligned (a single-dimension hint) suggests 4K alignment. It halves
the device bandwidth waste (938 to 469 MiB, A_byte 3.2× to 1.6×), a metric that looks like progress,
yet throughput does not improve (0.196 to 0.177 GiB/s, slightly worse). Alignment reduces bytes per op
but leaves 120,012 ops, and the workload is command-rate-bound, not bandwidth-bound. The effective lever
is coalescing (120k ops collapse to 301), which requires seeing both the per-op device bytes and the
command fan-out together, a view that per-op cross-layer attribution provides. Larger batches (the
cuFile-API-visible lever) also do nothing: the batch-size sweep showed device amplification invariant
at 3.2× across batch 1→128.

Both fixes that a partial or aggregate view suggests, aligning and batching more, fail; only the
layout/coalesce fix delivers the 13.7×. (Compare the command-amplification null result in
`interference.md`: a lever is not claimed until it is measured.)

## Why a formula does not replace the measurement
Computing the cost needs two inputs, neither of which is known in advance:
1. The per-op access geometry is produced inside the engine (NIXL batching, KV-cache layout, model
   sharding). It is not visible to an operator, and it changes with configuration.
2. The amplification granularity G is a property of the stack, not the device spec. On this node the
   device logical block is 512 B, but the measured granularity is 4096 B (ext4 block + O_DIRECT page
   alignment). Changing the FS, mount, LBA format, or `max_sectors_kb` changes G. G cannot be read off
   a datasheet; it appears only in the actual NVMe traffic the tracer observes.

Even with the formula, the alignment lever (O2) would be chosen unless the command fan-out is also
visible. The optimization is therefore driven by observation, not calculation.

## Limitations
- Single drive. The 13.7× applies to a KV-streaming-shaped workload where the per-class waste
  dominates. On a mixed job (§5.1b) the same fix is ~5% of the total, which is why aggregate tools do
  not surface it; per-class attribution shows which workloads the lever pays off on.
- Throughput is measured untraced; attribution comes from the traced run. Smaller KV (256 B layer
  access, 16×) amplifies more; 2560 B (PP8) is the representative size used here.
