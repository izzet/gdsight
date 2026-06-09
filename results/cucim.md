# cuCIM whole-slide imaging (digital pathology) under GDS-Trace — a real, non-obvious pathology

**The first non-obvious GDS pathology we found in the wild, on a flagship GDS workload.**

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

## Tool-guided fix + the trade-off
- **Coalesce adjacent tiles into ≥16 KiB reads** → cross the threshold → true GDS. (Recommended.)
- **Or `KVIKIO_GDS_THRESHOLD=0`** → all tiles GDS, but the small/unaligned tiles then amplify: 25.5 MiB
  requested → **42 MiB device = 1.65× byte-amp**. Forcing GDS isn't free — coalescing is the right fix.

(GDS-Trace shows *both* failure modes per-tile: the silent-POSIX split at the default threshold, and the
byte-amp when forced.)

## Honest scope
This is a **real, non-obvious pathology on a flagship GDS workload** — the demand proof we'd been missing: a
recognized digital-pathology GDS pipeline where GDS silently doesn't apply to 82% of the I/O, while the GDS
health tool (`gds_stats`) reports fine. Caveats: (1) it's kvikio's threshold behavior on the tile-read path
that cuCIM's own `gds_whole_slide` benchmark uses; (2) cuCIM's high-level `read_region(device="cuda")` may
route differently — it **hung** on our setup (GPU idle ~256 s; a separate issue worth its own look). Reproduce:
`workloads/cucim_gds_tiles.py` + `CMU-1.svs` (openslide-testdata).
