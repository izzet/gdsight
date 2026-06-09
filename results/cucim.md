# cuCIM whole-slide imaging (digital pathology) under GDS-Trace

**Honest one-liner:** the *naive per-tile* WSI GDS read path (which cuCIM's own `gds_whole_slide`
**benchmark** uses, and a naive user might write) silently bypasses GDS for ~82% of tiles and is ~19.5×
slower than coalescing — while cuCIM's **production `read_region(device="cuda")` API coalesces and is clean.**
GDS-Trace reveals *which path your code is on* and the lever; `gds_stats`/`iostat` can't.

## Workload (real, recognized GDS use case)
Real **Aperio SVS** (`CMU-1.svs`, 46000×32914, 23220 JPEG tiles, 256×256, **mean 6.7 KB/tile**, range
2.3–33.7 KB). Two GDS read paths exist for WSI:
- **(a) naive per-tile** kvikio reads — the path cuCIM's `examples/python/gds_whole_slide` benchmark uses
  (`workloads/cucim_gds_tiles.py`).
- **(b) production** `read_region(..., device="cuda")` — the high-level cuCIM API (`workloads/read_region_test.py`).

## Finding 1 — the naive per-tile path bypasses GDS for 82% of tiles
4000 random tiles via kvikio (default 16 KiB threshold):
| path | count | |
|---|--:|---|
| `cuFileRead` → `nvfs_io` (true GDS) | **712** | tiles ≥16 KiB |
| `pread64` (silent POSIX) | **3289** | tiles <16 KiB — **82%**, below kvikio's GDS threshold |

**Cross-check (measured):** `gds_stats`/cuFileGetStats sees 712 cuFile reads → *"GDS healthy"* (the 3289 POSIX
reads never enter cuFile); nvidia-fs +19 MiB (GDS tiles only); iostat +3788 device IOs (both paths, can't
split). **Only GDS-Trace, tracing both paths per-tile, names the 82% bypass.**

## Finding 2 — the production API coalesces (well-engineered, no bypass)
`read_region(device="cuda")`, 4000 patches: **108 `nvfs_io` (GDS) + 1 `pread64`** — it reads tiles in
**coalesced ~1 MiB GDS chunks** (108 reads, +108 MiB), *no* per-tile POSIX bypass. So cuCIM's main GDS API is
clean. (It also does **not** persistently hang — the earlier 256 s was a one-time cold GDS-init transient;
warm, 4000 patches take ~0.9 s.)

## The lever — verified 19.5× (and when it applies)
For a contiguous run of 4000 tiles (region scan; 0% gap), measured:
| strategy | reads | time | GDS |
|---|--:|--:|---|
| naive per-tile | 4000 small (6.7 KB) | 0.395 s | +20 MiB (mostly POSIX) |
| **coalesced 1 MiB GDS** | 26 | **0.020 s** | +26 MiB (true GDS) |
| | | **19.5×** | |

Coalescing (fewer/larger reads that also engage GDS) is exactly what `read_region` does internally.
**Access-pattern dependent:** *random* patch gather can't coalesce (scattered tiles) → GDS genuinely
under-delivers there, and forcing it (`KVIKIO_GDS_THRESHOLD=0`) is *worse* (1.65× byte-amp, slower).

## Honest scope
The 82% bypass is a **real anti-pattern** — but in the **naive per-tile path** (cuCIM's benchmark + naive
user code), **not** the production `read_region` API, which coalesces and is clean. This is consistent with
our recurring pattern: *well-engineered code is clean; naive code under-delivers.* GDS-Trace's value here is
diagnostic: it shows **which path your code takes** (per-tile bypass vs coalesced GDS) and the 19.5× lever —
which `gds_stats` (reports "healthy" either way) and `iostat` (can't split GDS vs POSIX) cannot. Reproduce:
`workloads/{cucim_gds_tiles,cucim_coalesce_test,read_region_test}.py` + `CMU-1.svs` (openslide-testdata).
