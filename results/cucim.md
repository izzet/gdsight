# cuCIM whole-slide imaging (digital pathology) under GDS-Trace — GDS silently under-delivers

**The first non-obvious GDS-vs-reality gap we found in the wild, on a flagship GDS workload: the marquee
"GDS-accelerated digital pathology" pipeline runs 82% on POSIX, the GDS health tools call it healthy, and
the real lever is I/O restructuring — not a flag.**

## Workload (real, recognized GDS use case)
Whole-slide-image (WSI) tiled reads are NVIDIA's marquee cuCIM + GDS example (digital pathology). Real
**Aperio SVS** (`CMU-1.svs`, 46000×32914, **23220 JPEG tiles**, 256×256, **mean 6.7 KB/tile**, range 2.3–33.7 KB),
read via **kvikio** — the GDS path cuCIM's own `gds_whole_slide` benchmark uses. `workloads/cucim_gds_tiles.py`,
4000 random tiles.

## Finding: 82% of WSI tiles silently bypass GDS
| path | count | what it is |
|---|--:|---|
| `cuFileRead` → `nvfs_io` (**true GDS**) | **712** | the ≥16 KiB tiles |
| `pread64` (**silent POSIX**) | **3289** | the <16 KiB tiles — **82%**, below kvikio's 16 KiB GDS threshold |

The flagship "GDS-accelerated WSI" pipeline leaves **82% of tile reads on the slow POSIX path** — because WSI
tiles (mean 6.7 KB) sit below the GDS threshold. **Non-obvious:** WSI is *the* GDS marketing workload, yet the
tile-size distribution defeats it.

## Cross-check: current tools vs GDS-Trace (measured)
| tool | what it reports | sees the 82% bypass? |
|---|---|:--:|
| `gds_stats` / cuFileGetStats | 712 cuFile reads, GDS active | **NO** — the 3289 POSIX reads never enter cuFile → "GDS healthy" |
| nvidia-fs `/proc` | readMiB **+19** (the GDS tiles only) | **NO** — POSIX reads don't reach nvidia-fs |
| `iostat` / diskstats | **+3788** device IOs (both paths) | **NO** — can't split GDS vs POSIX per tile |
| **GDS-Trace** | **712 `cuFileRead` + 3289 `pread64`**, per tile | **YES** — names the 82% bypass |

The GDS-specific tools report **"healthy"** precisely *because* the bypassed tiles never enter the
cuFile/nvidia-fs path — they're invisible to a GDS counter. Only tracing **both** paths in one per-op view
(ours) reveals that most of the workload isn't on GDS at all.

## Is the bypass even bad? (the honest nuance)
**Forcing GDS is *worse*, not better:** `KVIKIO_GDS_THRESHOLD=0` made all tiles GDS but ran **slower (0.82 s
vs 0.75 s)** and amplified **25.5 MiB → 42 MiB device = 1.65×**. So the bypass is *largely correct* — GDS's
setup cost isn't worth it for sub-16 KiB tiles (that's *why* kvikio's threshold exists). The finding is **not**
"flip a flag." It's two-fold:
1. **GDS is barely engaged for the flagship GDS workload** — only 18% of WSI tiles actually use it — and the
   GDS health tools can't tell you that (they see the 712 reads and call it healthy).
2. **To actually benefit from GDS on WSI you must restructure the I/O** — coalesce adjacent tiles into
   ≥16 KiB contiguous reads (read a tile *row/region* at once), not flip the threshold. GDS-Trace reveals the
   gap *and* points to the real lever (per-tile sizes), which `gds_stats`/`iostat` cannot.

## Honest scope
A **real, non-obvious finding on a flagship GDS workload**: the marquee "GDS-accelerated digital pathology"
pipeline mostly runs on POSIX (82% of tiles), the GDS health tool reports fine, and forcing GDS is
counter-productive — the genuine lever is I/O restructuring, which only per-op cross-layer tracing surfaces.
This is the closest we've come to *demand* — a recognized workload where GDS silently under-delivers and the
standard tools are blind to why. Caveats: (1) it's kvikio's threshold behavior on the tile-read path cuCIM's
own `gds_whole_slide` benchmark uses; (2) cuCIM's high-level `read_region(device="cuda")` **hung** on our
setup (GPU idle ~256 s) — unverified whether it bypasses similarly; worth its own look. Reproduce:
`workloads/cucim_gds_tiles.py` + `CMU-1.svs` (openslide-testdata).
