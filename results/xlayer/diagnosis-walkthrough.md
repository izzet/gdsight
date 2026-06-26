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
GDS-Trace attributes per op, across layers, so it reports not a device total but a per-class law:

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
| **align 4K (GDS-Trace)** | **7.81 MiB** | **1.60×** | 94.0 | **device bandwidth halved** (free layout change) |
| **coalesce (GDS-Trace)** | **4.94 MiB** | **1.01×** | **170.1** | **amplification eliminated + 1.8× goodput** |

Coalescing collapses both the wasted bytes (3.2×→1.0×, the drive now reads only what is used) and the
command count (2000 tiny reads → large aligned reads), yielding a measured **1.8× goodput** on the same
drive. Alignment alone is a free layout change that **halves the device bandwidth** the class consumes;
its wall-clock payoff is latent on an idle drive (the small-read stream is latency-bound) and is
realized under bandwidth contention — multi-tenant drives (our setup) or many concurrent serving
streams — exactly where KV-offload runs.

## The takeaway (the necessity claim, demonstrated)
Two tools, two fixes, one correct:
- **Current tools** → "device busy, GDS healthy" → *add concurrency* → A_byte stays 3.20×, goodput flat.
- **GDS-Trace** → "class-B amplifies 3.20× from sub-4K-misaligned reads" → *align/coalesce* → A_byte
  1.0×, 1.8× goodput.

The difference is not resolution for its own sake. The per-op, cross-layer, address-based attribution is
what turns an unactionable "the device is busy" into an actionable, *correct* "fix the KV read
geometry" — and we showed the obvious alternative, derived honestly from the tools people use today,
does not work.
