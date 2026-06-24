# Cross-layer GDS data-path characterization (Study A/B) — 2026-06-24

Instrument-first: use per-op cuFile↔nvidia-fs↔NVMe correlation (corr_id) to measure how one
application read transforms into device commands+bytes across the parameter space. gdsio as a clean
generator (sweeps, not cherry-picked workloads). Tool: `tools/xlayer_amp.py`, sweep
`tools/xlayer_sweep.sh`, data `results/xlayer/amp_*.csv`.

## Result 1 — the command-amplification law (GDS path, aligned sequential, single PM983)

| cuFile size | A_cmd (nvme cmds/op) | A_byte | device max cmd |
|---|--:|--:|--:|
| 4 KiB | 1.00 | 1.000 | 4 KiB |
| 64 KiB | 1.00 | 1.000 | 64 KiB |
| 256 KiB | 1.00 | 1.000 | 256 KiB |
| 1 MiB | 1.00 | 1.000 | 1024 KiB |
| **4 MiB** | **4.00** | 1.000 | **1280 KiB** |

**Law:** `A_cmd(S) = ⌈S / cap⌉`, `cap = 1280 KiB`; `A_byte = 1.000` (no byte inflation on the aligned
GDS path). ~4 unattributed device cmds/run (background/metadata) — honest, negligible.

## Result 2 — which stacked limit binds (the part that needs the cross-layer view)

There are **two** chunking limits above the device, plus the hardware cap:
- cuFile `max_direct_io_size_kb` (NVIDIA docs: GDS issues IO in chunks of this; ≤16 MiB)
- block-layer `max_sectors_kb` = **1280** (soft cap)
- hardware `max_hw_sectors_kb` = **2048** (NVMe MDTS proxy)

The per-op data shows the **block-layer soft cap (1280 KiB) binds — below both the cuFile chunk size
and the hardware MDTS (2048).** So default large GDS reads are split into ⌈S/1280⌉ commands when the
hardware would allow ⌈S/2048⌉. **Causally confirmed by the lever:** setting `max_sectors_kb=2048`
changed 4 MiB from **A_cmd 4→2** and 2 MiB from **2→1** (device cmd size 1280→2048 KiB), exactly as
predicted. `tools/xlayer_sweep.sh ... TAG=x0_hw2048`.

## Honest positioning (post-cuCIM discipline)

- **NOT a discovery of the knob.** `max_sectors_kb` is a documented general NVMe tuning parameter;
  cuFile documents `max_direct_io_size_kb`. We do not claim to have found these.
- **What IS the contribution:** (1) per-op attribution of *device-command count to the causing cuFile
  op* — no existing tool does this (gds_stats sees the cuFile op; iostat sees device IOs in aggregate;
  neither ties them); (2) using that to **measure which of the stacked limits actually binds** (block
  soft cap, below cuFile chunk + hw MDTS) and **causally confirm it with the lever**; (3) it doubles as
  **instrument validation** — the tool cleanly recovers a known, spec-defined mechanism (⌈S/cap⌉),
  evidence the per-op correlation is correct.
- **Open: does over-splitting cost anything?** A_cmd↓ matters only if it moves throughput/latency/CPU.
  On a single device at the BW ceiling it may not — must measure (tie A_cmd → perf) before claiming a
  lever worth pulling.

## Result 3 — does over-splitting COST anything? NO (single drive). ⚠️ kills the lever as a headline

Untraced gdsio throughput + per-op latency, GDS seq, cold cache, N=5, `max_sectors_kb` 1280 vs 2048
(`tools/amp_perf.sh`, `results/xlayer/amp_perf.csv`). Pairwise (same size, same threads):

| size | A_cmd 1280→2048 | GiB/s @1280 | GiB/s @2048 | latency @1280 | @2048 |
|---|---|--:|--:|--:|--:|
| 4M, 1 thread | **4→2** | 2.443 | 2.388 | 1584 µs | 1622 µs |
| 8M, 1 thread | **7→4** | 2.557 | 2.489 | 3004 µs | 3091 µs |
| 16M,1 thread | **13→8** | 2.672 | 2.705 | 5660 µs | 5590 µs |
| 4M, 4 thread | 4→2 | 2.937 | 2.933 | 5211 µs | 5235 µs |
| 16M,4 thread | 13→8 | 2.924 | 2.932 | 19770 µs | 19705 µs |

**Halving the device-command count changes throughput and latency by ~0** (differences are within
noise; at 1 thread the *more-split* config is even marginally faster). The single device is
**bandwidth-bound**; per-command overhead is fully hidden. ⇒ **`max_sectors_kb` is NOT a GDS speed
lever on a single drive — do not headline it.** (Honest kill of a tempting overclaim.)

Secondary real signal (Study B): single-thread throughput **rises with op size** (1M 1.90 → 16M 2.67
GiB/s) while 4-thread sits at the ~2.93 ceiling for all sizes — i.e. small single-thread ops are
**per-OP-overhead/latency-bound, not per-COMMAND-bound** (the cap makes no difference even where you're
below the ceiling). The bottleneck is the cuFile op rate + queue depth, not command splitting.

**Where command-amplification *would* matter (scope / future work, needs hardware we lack):**
command-rate-bound regimes — striped/multi-device (fabric not the limit), completion-CPU-bound, or
very high queue depth. Single A100 + one PM983 cannot exercise these.

## Result 4 — alignment read-amplification law (A_byte): real device-bandwidth tax ✅

Controlled cuFile (mis)alignment via `workloads/gds_align_probe.c` (full control of file_offset,
size, device-ptr offset), traced, per-op `A_byte = device bytes / requested bytes`
(`tools/align_sweep.sh`, `results/xlayer/align.csv`). 2000 ops/case, single PM983.

| case | iosize | file-off mis | dev-ptr mis | **A_byte** | device reads |
|---|--:|--:|--:|--:|---|
| aligned | 64K | 0 | 0 | 1.000 | exact |
| offset +512 | 64K | 512 | 0 | **1.0625** | +1 block (68K for 64K) |
| offset +100 | 64K | 100 | 0 | **1.0625** | +1 block (any sub-4K offset) |
| **sub-4K** | **512** | 0 | 0 | **8.000** | full 4K block for a 512 B read |
| sub-4K + off | 512 | 512 | 0 | **8.000** | full 4K block |
| size 4095 | 4095 | 0 | 0 | 1.0002 | rounds to 4K |
| **dev-ptr +512** | 64K | 0 | **512** | **1.000** | **GPU-ptr misalignment is FREE at the device** |

**Law:** `A_byte = ⌈(file_off mod 4K + size)/4K⌉·4K / size`. The device read granularity is **4 KiB**:
sub-4K reads pay up to **8×** (512 B → 4 KiB); any sub-4K-misaligned *file* offset adds exactly one
block. Cross-validates the documented 4K-alignment requirement — and **refines it**: of the three
things the docs lump together ("file_offset, size, devPtr"), only the **file-side** two amplify
*device* reads; **device-pointer misalignment costs nothing at the device** (handled GPU-side). That
asymmetry is not in the docs and is GDS-specific (the GPU pointer is the GDS-unique element).

**Why this one matters (unlike A_cmd):** A_byte is a *direct device-bandwidth tax* — at A_byte=8, 7/8
of consumed device bandwidth moves bytes the app never asked for. No separate perf run needed: the
amplification *is* the waste (contrast Result 3, where extra commands were free). Translating to
end-app throughput depends on BW- vs IOPS-bound regime, but the device-bandwidth waste is direct.

**Honest positioning:** sub-block read-amplification is a known storage phenomenon (true for any
O_DIRECT reader, CPU too) — we do **not** claim to have discovered it. The contributions: (1) **per-op
cross-layer attribution** of it in the GDS path — `gds_stats` reports the 512 B request and `posix=0`
("healthy"); `iostat` sees 4 KiB device reads in aggregate but cannot tie them to the op or know
they are 8× the request; only per-op cuFile↔NVMe byte-correlation names it; (2) the measured
**file-side vs GPU-ptr asymmetry**; (3) the link to real GDS workloads — small-granularity access
(WSI tiles ~6.7 KB, embedding/KV gathers) silently pays this.

**Emerging coherent thesis:** small-granularity access is the silent killer for GDS, in *two distinct*
cross-layer ways aggregate tools miss — (1) below kvikio's 16 KB threshold → silent POSIX bypass
(cuCIM naive path), (2) below the 4 KiB device granularity → read-amplification (this). Per-op
cuFile↔nvidia-fs↔NVMe attribution is what surfaces both.

## Result 5 — the law manifests in a REAL workload (CMU-1.svs WSI tiles) ✅ predicted = measured

Extracted the *actual* level-0 tile geometry from a real Aperio SVS (CMU-1.svs, 23,220 JPEG tiles,
mean 6.77 KB, range 2.3–33.7 KB): **0.0% have a 4K-aligned file offset, 0.0% a 4K-multiple size**
(JPEG tiles packed back-to-back at arbitrary byte offsets). Replayed those exact reads through GDS
(`workloads/gds_replay.c` ← `/tmp/tiles.csv`), traced, measured per-op A_byte:

| | mean A_byte | median | bytes-weighted (device/requested) |
|---|--:|--:|--:|
| predicted from geometry (the law) | 2.326 | 1.745 | 1.605 |
| **measured (traced through GDS)** | **2.326** | **1.745** | **1.605** |

**Exact match.** A real digital-pathology slide read through GDS moves **1.6× the requested bytes at
the device** (~38% of device read-bandwidth spent on alignment padding), 78.5% of tiles amplified
>1.5×. The geometry-only prediction nails the measured device behavior → the instrument is validated
*and* the phenomenon is real, not constructed. `gds_stats` reports the 149.8 MiB requested + `posix=0`
("healthy"); `iostat` sees the inflated device bytes only in aggregate; **only per-op cuFile↔NVMe
byte-correlation attributes the 1.6× to the tile reads and predicts it from geometry.**

**Honest scope:** the 4K read-amplification is a property of (small, unaligned reads) × (4K block
device) — **not GDS-specific** (a CPU O_DIRECT reader of the same tiles pays it too). We do not claim
GDS causes it. The contributions are (1) **per-op cross-layer attribution** of the tax in a real GDS
workload (no existing tool does this), (2) it is **predictable from access geometry** (a closed-form
law, validated), (3) the **fix is layout** (4K-align/pad tiles ⇒ A_byte→1.0, predicted) — not a GDS
flag. This answers "is it cherry-picked?": the law is general and *manifests* in real data; the SVS is
the test vehicle, not a hand-built gotcha.

### The fix ladder (validated law ⇒ prediction = truth) — and it unifies with cuCIM

| strategy | A_byte (device/useful) | |
|---|--:|---|
| naive per-tile, real offsets | **1.605** | measured ✓ |
| per-tile, 4K-aligned offsets | 1.246 | removes offset-spill; size-rounding floor remains |
| **coalesced (contiguous span)** | **1.000** | tiles are packed back-to-back ⇒ ~no waste |

**Coalescing is the fix — the *same* lever as the cuCIM bypass finding** (`read_region` coalesces).
This unifies the two granularity taxes: naive per-tile access pays **both** (a) silent POSIX bypass
below kvikio's 16 KB threshold *and* (b) 1.6× device read-amplification below the 4 KiB device
granularity; coalescing eliminates both. Our per-op cross-layer tracer is what quantifies the
device-layer 1.6× that `gds_stats`/`iostat` cannot attribute, and predicts it from access geometry.

## Next arms (where genuinely new findings should live)
- **Alignment read-amplification (Study A, alignment axis):** NVIDIA docs say GDS "uses internal cache
  when file_offset/size/ptr are not 4K aligned" → measure per-op A_byte>1 / path change on misaligned
  GDS reads. Quantifies a documented-but-unmeasured behavior.
- **GDS-vs-bounce device divergence (Study C):** same app I/O on `-x0` (GDS) vs `-x2` (bounce): does the
  page-cache/readahead path inflate device bytes (A_byte>1) or change the LBA/cmd stream vs O_DIRECT GDS?
- **Disentangle the two chunk limits:** vary cuFile `max_direct_io_size_kb` in cufile.json and re-measure
  to confirm block layer binds independently.
- **Amplification → performance:** correlate A_cmd with throughput/latency to see if the split costs.

Sources: NVIDIA GDS Configuration/Best-Practices/Troubleshooting guides
(docs.nvidia.com/gpudirect-storage/).
