# From observation to fix: a diagnostic walkthrough (why current tools mislead, why ours doesn't)

**Purpose.** Necessity, made falsifiable. We take a real KV-offload pipeline that current tools call
healthy, follow the fix a practitioner would *actually derive from those tools*, show it **fails**
(measured), then show the fix **our per-op attribution prescribes works** (measured). The point is not
"our tool shows more detail" — it is that *the two tools lead to two different fixes, and only ours is
correct.*

Engine: real NIXL `cuda_gds` (batched, async). Workload: 2000 × 2560 B KV reads (Llama-3.1-70B PP8),
file→GPU true P2P. Reproduce: `tools/run_nixl_diagnosis.sh` (device bytes, traced) +
`tools/goodput x3` (cold, untraced). Data: `results/xlayer/nixl_diagnosis.csv`.

---

## Step 1 — The symptom (visible to anyone)
The pipeline requests **4.88 MiB** of KV data, but the **drive reads 15.6 MiB** to deliver it. Useful
bandwidth is **31%** of device bandwidth — the GPU waits on storage that is "busy" doing 3× the work it
should. This *is* a bandwidth problem, and it is visible.

## Step 2 — What current tools report, and the fix they lead to
| Tool | Reading | Natural inference |
|---|---|---|
| `gds_stats` / `cuFileGetStats` | 2000 reads, **all on GDS — "healthy"** | GDS is engaged; not a fallback problem |
| `nvidia-fs` `/proc` stats | **16 MiB** GDS reads | the GDS path is moving real bytes |
| `iostat` / diskstats | **15 MiB** device reads, drive busy | **"I'm I/O-bound; the drive is the ceiling"** |

Every current observer reports the same thing — *the device is moving ~15 MiB and is busy* — and none
can say whether that 15 MiB is useful work or padding. The standard inference from "GDS healthy + device
saturated" is **"add concurrency / deepen the queue to extract more bandwidth"** (or, worse, "buy faster
storage"). It is the obvious, defensible move given what these tools show.

## Step 3 — The obvious fix fails (measured)
Double the GDS batch (64 → 128), the textbook "extract more throughput" lever:

| | device bytes (ours) | A_byte | goodput (3 cold runs, mean) |
|---|--:|--:|--:|
| baseline (batch 64) | 15.62 MiB | 3.20× | **94.7 MiB/s** |
| **obvious fix: batch 128** | **15.62 MiB** | **3.20×** | **65.5 MiB/s** (no gain — slightly worse) |

The drive still moves exactly 15.62 MiB; goodput does **not** improve (in fact it drops, deeper queue =
more overhead on a latency-bound stream). The amplification is invariant to concurrency — a fact we also
establish across batch 1→128 (all 3.20×, `nixl-e2e-complete.md` §2b). **You cannot tune your way out of
this with anything the current tools suggest, because they never revealed what the wasted bytes are.**

## Step 4 — What *our* tool additionally shows, and how the fix falls out of it
GDSight attributes per op, across layers, so it reports not a device total but a per-class law:

> class-B KV reads: **A_byte = 3.20×** — each 2560 B `cuFileBatchIOSubmit` op maps to an **8192 B** span
> of `nvme_setup_cmd`s, with `nvfs_get_p2p_dma_mapping` firing (true P2P, not staging), at file offsets
> with **`off mod 4K = 3072`**.

That is a *mechanism*, not a symptom. The closed form `A_byte = ⌈(off mod 4K + size)/4K⌉·4K / size`
reads straight off the attributed data and **prescribes the fix**: the 3.2× is two 4 KiB blocks moved
per 2560 B read because the read is sub-4K *and* misaligned. Remove either cause:
- **align** the KV records to 4 KiB (`off mod 4K = 0`) → predicted 1 block / 2560 B = **1.6×**;
- **coalesce** KV into contiguous 4K-multiple reads → **1.0×**.

No current tool exposes per-op offset geometry, so none can even form this hypothesis — the only
hypotheses they support are "more parallelism" or "more hardware," both of which leave A_byte at 3.2×.

## Step 5 — The prescribed fix works (measured)
| fix (source) | device bytes | A_byte | goodput (mean) | effect |
|---|--:|--:|--:|---|
| baseline | 15.62 MiB | 3.20× | 94.7 MiB/s | — |
| obvious: batch 2× (current tools) | 15.62 MiB | 3.20× | 65.5 | **no help** |
| **align 4K (GDSight)** | **7.81 MiB** | **1.60×** | 94.0 | **device bandwidth halved** (free layout change) |
| **coalesce (GDSight)** | **4.94 MiB** | **1.01×** | **170.1** | **amplification eliminated + 1.8× goodput** |

Coalescing collapses both the wasted bytes (3.2×→1.0×, the drive now reads only what is used) and the
command count (2000 tiny reads → large aligned reads), yielding a measured **1.8× goodput** on the same
drive. Alignment alone is a free layout change that **halves the device bandwidth** the class consumes;
its wall-clock payoff is latent on an idle drive (the small-read stream is latency-bound) and is
realized under bandwidth contention — multi-tenant drives (our setup) or many concurrent serving
streams — exactly where KV-offload runs.

## Step 6 — Why this is *not* "just align to 4K" (the per-op amplification is the measured part)
The obvious reviewer objection is: *"aligning/coalescing I/O is textbook; the docs tell you the block
size; you'd do it in the baseline."* The 4 KiB grid *is* documented (it is the ext4 block size — a single
`tune2fs`). Our point is narrower and survives that: (1) the problem is invisible to the tools a
practitioner consults (gds_stats/iostat read healthy), and (2) the **per-op amplification against the
grid** — which op-class pays it and how much — is what no tool exposes, and it is what targets the fix.

A practitioner asks "what is the block size to align to?" The natural place to check — the *device* —
answers **512 B** (logical and, on this drive, physical), which predicts *no problem at all*:

| Where you'd look up the granularity | Reports | Predicted A_byte for a 2560 B aligned read |
|---|--:|--:|
| device LBA (`/sys/.../logical_block_size`) | **512 B** | 5×512 = exact → **1.00× ("aligned, fine")** |
| NVMe `physical_block_size` / `minimum_io_size` | **512 B** | **1.00×** |
| ext4 block size (`tune2fs`) | 4096 B | 1.60× |
| **measured effective, GDS path (GDSight)** | **4096 B** | **1.60× aligned / 3.20× as laid out** |

(Reproducible: `tools/probe_effective_granularity.sh` → `results/xlayer/effective-granularity.txt`,
which measures device-bytes-per-aligned-read = 4096 for every request size 512…4096 B, 8192 for 6144 B —
i.e. `ceil(S/4096)·4096`, never the `ceil(S/512)·512` the spec predicts.)

The device — the natural place a storage engineer checks for "block size" — says **512 B**, so
first-principles arithmetic says 2560 B is 5 perfectly-aligned sectors, **1.00×, nothing to fix**. The
binding grid is actually the **4096 B ext4 block** (documented via `tune2fs`; it holds on the true-P2P
path — an O_DIRECT 512 B read moves 4096 B), so aligning to the device's 512 B leaves the 3.2× in place;
the grid to align to is **4096**. The 4 KiB constant is one query — *not* a discovery. What is **not**
queryable a priori is the **per-op amplification**: that the KV class lands sub-4K *and misaligned*
(off mod 4K = 3072 → 8192 B per 2560 B = 3.2×, vs 1.6× if aligned, vs 1.0× if coalesced) depends on the
access geometry × file layout, and **only per-op cross-layer measurement gives it**. It is also
**different on another system** (a 4Kn device, a 1 KiB/2 KiB-block FS, XFS, or a larger controller mapping
unit change the grid; the access pattern changes the factor). On such a system the amplification — and the
right fix — changes; GDSight re-derives
it by measurement, documentation cannot.

And the **magnitude is equally a measured, per-(size×system) quantity, not a constant**: across the same
real NIXL KV sizes, "align/coalesce" buys nothing-to-everything — `A_byte` = 1.6× (10240 B) … 3.2×
(2560 B) … **16×** (256 B layer-access) (`nixl-e2e-complete.md` §2). Whether the fix is even worth doing,
and how much it returns, is set by where the model-determined access size falls on *this* device's
effective grid — which you measure, not assume.

## The takeaway (the necessity claim, demonstrated)
Three observers, three wrong moves, one correct fix — and the correct fix is *quantitative and
system-specific*:
- **gds_stats / iostat** → "device busy, GDS healthy" → *add concurrency* → A_byte stays 3.20×, no gain.
- **the spec sheet / device block size (512 B)** → "2560 B is aligned, 1.00×" → *do nothing* → 3.2× waste persists.
- **GDSight** (requested-vs-device, per op, cross-layer) → "effective granularity is **4096 B**, this
  class amplifies **3.20×**" → *align/pad to the measured 4096 and coalesce* → A_byte 1.0×, 1.8× goodput.

The contribution is not the textbook lever. It is the cross-layer measurement that, **on this specific
system**, tells you (a) a problem exists at all (every other view says healthy), (b) how large it is
(1.0×–16× — worth fixing or not), and (c) the exact granularity to fix it to (4096, which the device
spec reports as 512). None of (a)–(c) is derivable from any single layer's documentation, and all three
change across systems — which is precisely why the measurement, not the maxim, is the necessary part.
