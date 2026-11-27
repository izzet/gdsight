# Cross-operation interference and tail-latency attribution

> **Note on values.** This note records an earlier single run with unrecorded contention parameters.
> It was re-measured at a documented contention level (4 small threads of 3 KiB against 4 large
> readers of 4 MiB, n=3). Current values: isolated p99 214.3+/-5.1 us, contended p99 4790.7+/-139.4
> us, inflation 22.35x (not 19x, not 216 -> 4080). The median moves in the opposite direction to what
> is recorded below: p50 rises 9% (136.3 -> 148.7 us) rather than falling. The `max_sectors_kb`
> comparison was likewise re-measured: p99 4758+/-138 us at cap 1280 against 4946+/-106 us at 2048, a
> difference that is not statistically significant. Sources: `tail_reps.csv`, `cap_tail_reps.csv`.

## Setup

Question: do concurrent large GDS reads inflate the tail latency of small GDS ops through device
head-of-line blocking, and can that be attributed per op only with cross-layer tracing?

Probe: `workloads/gds_interfere.c` (multi-threaded sync `cuFileRead`, so the trace `dur` is the true
per-op latency; verified: 1 MiB reads p50≈1334 µs, as expected). Scorers: `tools/interfere_score.py`,
`tools/interfere_attrib.py`.

## Result: bimodal tail inflation not visible in the median

Small ops are 3 KiB scattered reads; contention is concurrent large reads.

| small-op latency (µs) | alone | + large 1 MiB | + large 4 MiB |
|---|--:|--:|--:|
| p50 | 141 | 141 | 136 |
| p90 | 164 | 981 | 186 |
| p99 | 216 | 1182 (5.5×) | 4080 (19×) |
| max | 2210 | 3862 | 5551 |

The median is unchanged while the tail grows up to 19×. Mean, median, and aggregate monitoring (and
`gds_stats`) would report this as healthy. The p99 (~4080 µs) is close to one large-op latency
(~5300 µs), which indicates the mechanism: the slow small ops were queued behind a large read at the
device.

## Cross-layer attribution

For each small op, count the large-region NVMe commands issued during its `[ts, ts+dur]` window:

| small op | large device cmds overlapping its window |
|---|--:|
| FAST (≤ p50) | 0.00 |
| SLOW (≥ p90) | 3.2–8.0 |

Slow ops coincide with concurrent large device activity deterministically (fast ops overlap zero).
This gives the per-op root cause: a specific small op was slow because specific large-read device
commands occupied the device during its window. The application side alone shows only that an op was
slow, without a cause; the device side alone shows a busy queue, without identifying the op. Only the
cross-layer per-op timeline establishes the link.

## Follow-up 1: does `max_sectors_kb` control the tail? (refuted)

Hypothesis: `max_sectors_kb` (throughput-neutral, Result 3) controls the tail, because larger device
commands mean longer indivisible occupancy and worse head-of-line blocking. Refuted: at 4 MiB large
reads, small-op p99 = 4080 µs (cap 1280) vs 4058 µs (cap 2048), effectively identical. Command chunking
is conserved-occupancy: slow ops overlap fewer but larger commands (7.96 to 4.96) for the same tail. So
`max_sectors_kb` is both throughput- and tail-neutral; the tail tracks large-read bytes in flight, not
command size.

## Follow-up 2: is GDS-P2P contention worse than CPU-bounce contention? (null)

Does GDS-P2P large-read contention hurt small GDS ops more than CPU-bounce large reads (shared GPU
PCIe/BAR1 versus a separate host path)? Same small (GDS) load; large reads via GDS P2P versus CPU host
O_DIRECT (`gds_interfere.c` arg `large_cpu`). Small-op p99: GDS-large 1227 us vs CPU-large 1267 us,
identical within noise. There is no GDS-specific interference: contention is at the shared NVMe queue
(the same device commands on either path), not the GPU link (PCIe/BAR1 is nowhere near saturated at
2.9 GiB/s on one drive). The result might differ on PCIe-saturated or multi-drive hardware, which
could not be tested here.

## Interpretation and scope

- Head-of-line blocking is a known storage phenomenon; its existence is not new. What this note adds
  is that (a) it is median-invisible (only the tail moves, so standard monitoring misses it), (b) its
  magnitude (up to 19× p99), and (c) per-op attribution to the interfering ops.
- An application needs no tracer to detect its own tail latency. For root-cause attribution (which ops
  or layer caused it), the cross-layer per-op timeline is necessary: `gds_stats`/`iostat` cannot
  attribute tail latency to an interfering op.
- The workload is a mixed-size concurrent pattern representative of multi-tenant or mixed pipelines,
  but it is constructed, with the same caveat as the other controlled experiments.
- Neither follow-up found new device or GDS-specific behaviour. The result is a tail-latency
  root-cause attribution capability that is hard to obtain any other way, consistent with the other
  notes: the contribution is per-op cross-layer attribution, not new device behaviour.
