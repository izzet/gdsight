# Cross-op interference / tail-latency attribution (2026-06-25)

> **SUPERSEDED, 2026-08-06.** Single run with unrecorded contention parameters. Re-measured at a
> documented contention level (4 small threads of 3 KiB against 4 large readers of 4 MiB, n=3).
> Current values: isolated p99 **214.3+/-5.1 us**, contended p99 **4790.7+/-139.4 us**, inflation
> **22.35x** (not 19x, not 216 -> 4080). Note the median moves in the opposite direction to what is
> recorded below: p50 **rises 9%** (136.3 -> 148.7 us) rather than falling. The `max_sectors_kb`
> comparison is likewise re-measured: p99 **4758+/-138 us** at cap 1280 against **4946+/-106 us** at
> 2048, a difference that is not statistically significant. Sources: `tail_reps.csv`,
> `cap_tail_reps.csv`. Kept as the record of the earlier run.

Hunt for a genuine discovery: do concurrent large GDS reads inflict tail latency on small GDS ops via
device head-of-line blocking, attributable per-op only cross-layer? Probe `workloads/gds_interfere.c`
(multi-thread sync cuFileRead → trace `dur` is the true per-op latency; verified: 1 MiB reads p50≈1334
µs ≈ expected). Scorers `tools/interfere_score.py`, `tools/interfere_attrib.py`.

## Result — severe, bimodal, median-invisible tail inflation

small ops = 3 KiB scattered; contention = concurrent large reads:

| small-op latency (µs) | alone | + large 1 MiB | + large 4 MiB |
|---|--:|--:|--:|
| **p50** | 141 | **141** | **136** |
| p90 | 164 | 981 | 186 |
| **p99** | 216 | 1182 (5.5×) | **4080 (19×)** |
| max | 2210 | 3862 | 5551 |

**The median is untouched; the tail explodes up to 19×.** Mean/median/aggregate monitoring (and
`gds_stats`) would call this perfectly healthy. The p99 (~4080 µs) ≈ one large-op latency (~5300 µs),
which *names the mechanism*: the slow small ops were stuck behind a large read at the device.

## Cross-layer attribution (the part only per-op tracing can do)

For each small op, count large-region NVMe commands issued during its `[ts, ts+dur]` window:

| small op | large device cmds overlapping its window |
|---|--:|
| FAST (≤ p50) | **0.00** |
| SLOW (≥ p90) | **3.2–8.0** |

Slow ⟺ concurrent large device activity, **deterministically** (fast ops overlap *zero*). This is the
per-op root cause: a specific small op was slow *because these specific large-read device commands
occupied the device during its window*. App-side alone: "this op was slow" (no why). Device-side
alone: "queue busy" (which op? can't say). **Only the cross-layer per-op timeline establishes it.**

## The non-obvious twist we tested — and it was REFUTED (honest null)

Hypothesis: `max_sectors_kb` (throughput-neutral, Result 3) secretly controls the tail — bigger device
commands = longer indivisible occupancy = worse HoL. **Refuted:** at 4 MiB large reads, small-op
p99 = 4080 (cap 1280) vs 4058 (cap 2048) — *identical*. Command **chunking is conserved-occupancy**:
slow ops overlap fewer-but-larger commands (7.96→4.96) for the **same** tail. So `max_sectors_kb` is
both throughput- and tail-neutral; the tail tracks large-read **bytes in flight**, not command size.

## Honest assessment (against the bar)

- **Non-obvious: PARTIAL.** Head-of-line blocking is a *known* storage phenomenon — the existence is
  not novel. The non-obvious/valuable parts are (a) it is **median-invisible** (only the tail moves, so
  standard monitoring misses it), (b) the **magnitude** (up to 19× p99), and (c) the **per-op
  attribution** to the interfering ops.
- **Method-necessary: PARTIAL.** For *detecting* its own tail an app needs no tracer; for **root-cause
  attribution** (which ops/layer caused it) the cross-layer per-op timeline is necessary — `gds_stats`/
  `iostat` cannot attribute tail latency to an interfering op.
- **Constructed:** realistic mixed-size concurrent pattern (any multi-tenant / mixed pipeline), but
  built by us — same caveat as the other controlled demos.

**Standing:** not the novel-physics discovery hoped for (HoL blocking is known). It is a clean,
rigorous **tail-latency root-cause attribution** capability — genuinely hard to get any other way —
and reinforces the consistent theme: the contribution is *per-op cross-layer attribution*, not new
physics. Open GDS-specific angle (untested): is GDS P2P interference worse than CPU-bounce (BAR1 /
non-reorderable P2P commands)? That could be a genuinely GDS-specific result.

## GDS-specific angle tested — NULL (honest)
Does GDS-P2P large-read contention hurt small GDS ops more than CPU-bounce large reads (shared GPU
PCIe/BAR1 vs separate host path)? Same small (GDS) load; large reads via GDS P2P vs CPU host O_DIRECT
(`gds_interfere.c` arg `large_cpu`). Small-op p99: **GDS-large 1227 us vs CPU-large 1267 us — identical
(within noise).** No GDS-specific interference: contention is at the shared NVMe queue (same device
commands either path), not the GPU link (PCIe/BAR1 nowhere near saturated at 2.9 GiB/s on one drive).
Might differ on PCIe-saturated/multi-drive hardware (untestable here). Confirms: no novel GDS-specific
physics; the contribution is per-op cross-layer attribution, not new device behavior.
