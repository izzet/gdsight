# Diagnostic walkthrough: from observation to fix

This walkthrough follows a real KV-offload pipeline that current tools report as healthy. It applies
the fix a practitioner would derive from those tools and measures that it fails, then applies the fix
that GDSight's per-op attribution prescribes and measures that it works. The two views lead to two
different fixes, and only the second one improves the pipeline.

## Setup
- Engine: real NIXL `cuda_gds` (batched, async).
- Workload: 2000 × 2560 B KV reads (Llama-3.1-70B PP8), file to GPU, true P2P.
- Reproduce: `tools/run_nixl_diagnosis.sh` (device bytes, traced) and `tools/goodput x3` (cold,
  untraced).
- Data: `results/xlayer/nixl_diagnosis.csv`.

---

## 1. Symptom
The pipeline requests 4.88 MiB of KV data, but the drive reads 15.6 MiB to deliver it. Useful
bandwidth is 31% of device bandwidth: the GPU waits on storage that is busy doing 3× the work it
should. This is a bandwidth problem, and it is visible.

## 2. What current tools report, and the fix they suggest
| Tool | Reading | Natural inference |
|---|---|---|
| `gds_stats` / `cuFileGetStats` | 2000 reads, all on GDS ("healthy") | GDS is engaged; not a fallback problem |
| `nvidia-fs` `/proc` stats | 16 MiB GDS reads | the GDS path is moving real bytes |
| `iostat` / diskstats | 15 MiB device reads, drive busy | "I'm I/O-bound; the drive is the ceiling" |

Every current observer reports the same thing: the device is moving ~15 MiB and is busy. None can say
whether that 15 MiB is useful work or padding. The standard inference from "GDS healthy, device
saturated" is to add concurrency or deepen the queue to extract more bandwidth (or to buy faster
storage). Given what these tools show, it is the obvious, defensible move.

## 3. The obvious fix fails (measured)
Double the GDS batch (64 to 128), the standard lever for extracting more throughput:

| | device bytes (GDSight) | A_byte | goodput (3 cold runs, mean) |
|---|--:|--:|--:|
| baseline (batch 64) | 15.62 MiB | 3.20× | 94.7 MiB/s |
| obvious fix: batch 128 | 15.62 MiB | 3.20× | 65.5 MiB/s (no gain; slightly worse) |

The drive still moves exactly 15.62 MiB, and goodput does not improve. It drops, because a deeper queue
adds overhead on a latency-bound stream. The amplification is invariant to concurrency; this also holds
across batch 1→128 (all 3.20×, `nixl-e2e-complete.md` §2b). No lever the current tools suggest changes
it, because they do not reveal what the wasted bytes are.

## 4. What GDSight additionally shows, and the fix it implies
GDSight attributes per op, across layers, so it reports a per-class result rather than a device total:

> class-B KV reads: A_byte = 3.20×. Each 2560 B `cuFileBatchIOSubmit` op maps to an 8192 B span
> of `nvme_setup_cmd`s, with `nvfs_get_p2p_dma_mapping` firing (true P2P, not staging), at file offsets
> with `off mod 4K = 3072`.

This is a mechanism rather than a symptom. The closed form `A_byte = ⌈(off mod 4K + size)/4K⌉·4K / size`
follows directly from the attributed data and prescribes the fix: the 3.2× is two 4 KiB blocks moved
per 2560 B read, because the read is both sub-4K and misaligned. Removing either cause helps:
- align the KV records to 4 KiB (`off mod 4K = 0`): predicted 1 block / 2560 B = 1.6×;
- coalesce KV into contiguous 4K-multiple reads: 1.0×.

No current tool exposes per-op offset geometry, so none supports this hypothesis. The only hypotheses
they support are more parallelism or more hardware, and both leave A_byte at 3.2×.

## 5. The prescribed fix works (measured)
| fix (source) | device bytes | A_byte | goodput (mean) | effect |
|---|--:|--:|--:|---|
| baseline | 15.62 MiB | 3.20× | 94.7 MiB/s | — |
| obvious: batch 2× (current tools) | 15.62 MiB | 3.20× | 65.5 | no help |
| align 4K (GDSight) | 7.81 MiB | 1.60× | 94.0 | device bandwidth halved (free layout change) |
| coalesce (GDSight) | 4.94 MiB | 1.01× | 170.1 | amplification eliminated, 1.8× goodput |

Coalescing reduces both the wasted bytes (3.2× to 1.0×; the drive now reads only what is used) and the
command count (2000 small reads become large aligned reads), giving a measured 1.8× goodput on the same
drive. Alignment alone is a free layout change that halves the device bandwidth the class consumes. Its
wall-clock payoff is latent on an idle drive, because the small-read stream is latency-bound; it is
realized under bandwidth contention, such as multi-tenant drives (as in this setup) or many concurrent
serving streams, which is where KV offload runs.

## 6. Why this is more than "align to 4K"
A common objection is that aligning and coalescing I/O is standard practice: the documentation gives the
block size, so a baseline would already do it. The 4 KiB grid is documented (it is the ext4 block size,
one `tune2fs` query). The claim here is narrower: (1) the problem is invisible to the tools a
practitioner consults (gds_stats and iostat read healthy), and (2) the per-op amplification against the
grid, meaning which op class pays it and how much, is not exposed by any tool, and it is what targets
the fix.

A practitioner asks which block size to align to. The natural place to check, the device, answers
512 B (logical and, on this drive, physical), which predicts no problem at all:

| Where you'd look up the granularity | Reports | Predicted A_byte for a 2560 B aligned read |
|---|--:|--:|
| device LBA (`/sys/.../logical_block_size`) | 512 B | 5×512 = exact → 1.00× ("aligned, fine") |
| NVMe `physical_block_size` / `minimum_io_size` | 512 B | 1.00× |
| ext4 block size (`tune2fs`) | 4096 B | 1.60× |
| measured effective, GDS path (GDSight) | 4096 B | 1.60× aligned / 3.20× as laid out |

Reproduce with `tools/probe_effective_granularity.sh`, which writes
`results/xlayer/effective-granularity.txt`. It measures device bytes per aligned read = 4096 for every
request size 512…4096 B and 8192 for 6144 B, i.e. `ceil(S/4096)·4096`, never the `ceil(S/512)·512` the
spec predicts.

The device reports 512 B, so first-principles arithmetic says 2560 B is 5 aligned sectors, 1.00×,
nothing to fix. The binding grid is the 4096 B ext4 block (documented via `tune2fs`; it holds on the
true-P2P path, where an O_DIRECT 512 B read moves 4096 B). Aligning to the device's 512 B therefore
leaves the 3.2× in place; the grid to align to is 4096. The 4 KiB constant is a single query, not a
discovery. What cannot be queried in advance is the per-op amplification: that the KV class lands
sub-4K and misaligned (off mod 4K = 3072, so 8192 B per 2560 B = 3.2×, versus 1.6× if aligned and 1.0×
if coalesced) depends on access geometry and file layout, and only per-op cross-layer measurement gives
it. It also differs on other systems: a 4Kn device, a 1 KiB or 2 KiB block file system, XFS, or a
larger controller mapping unit changes the grid, and the access pattern changes the factor. On such a
system the amplification and the right fix change; GDSight re-derives them by measurement, which
documentation cannot do.

The magnitude is likewise a measured, per-(size × system) quantity rather than a constant. Across the
same real NIXL KV sizes, aligning or coalescing returns anywhere from nothing to everything: `A_byte` =
1.6× (10240 B) … 3.2× (2560 B) … 16× (256 B layer access) (`nixl-e2e-complete.md` §2). Whether the fix
is worth doing, and how much it returns, depends on where the model-determined access size falls on this
device's effective grid, which has to be measured.

## Summary
Three observers lead to three different actions, and only one is correct; the correct fix is
quantitative and system-specific.
- gds_stats and iostat report a busy device and healthy GDS, which suggests adding concurrency. A_byte
  stays at 3.20× and there is no gain.
- The spec sheet and device block size (512 B) say 2560 B is aligned (1.00×), which suggests doing
  nothing. The 3.2× waste persists.
- GDSight (requested versus device bytes, per op, cross-layer) reports an effective granularity of
  4096 B and a class amplification of 3.20×, which suggests aligning or padding to the measured 4096 and
  coalescing. A_byte drops to 1.0× and goodput rises 1.8×.

The lever itself is standard. What the cross-layer measurement provides, on this specific system, is
(a) that a problem exists at all (every other view reads healthy), (b) how large it is (1.0×–16×, which
decides whether it is worth fixing), and (c) the exact granularity to fix it to (4096, which the device
spec reports as 512). None of (a)–(c) follows from any single layer's documentation, and all three
change across systems.
