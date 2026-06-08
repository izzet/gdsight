# GDS-Trace — Motivating evidence: the silent sub-threshold POSIX fallback that GDS tools can't see

**A rigorous, reproducible scenario** (Chameleon A100, true GDS) where a *realistic* workload silently
runs a large fraction of its reads on POSIX instead of GDS, and **`gds_stats` — NVIDIA's GDS
observability tool — reports `posix=0` ("GDS perfect") and cannot see it.** This is the corrected
Figure-1 (an earlier "bypass" claim was a measurement error; this one is verified against the kernel
nvidia-fs DMA counters with a `gdsio -x0` control). Scripts: `step3/step3_sizesweep.py`,
`step3/step3_mixed.py`. Ground-truth oracle: `/proc/driver/nvidia-fs/stats` (`rw_stats_enabled=1`).

> ## ⚠️ REALITY CHECK — this is kvikio-SPECIFIC, not a GDS-intrinsic gap
> Verified on the same node: **raw cuFile (`gdsio -x0 -i 4K`) DMAs 4 KiB reads** (kernel Δreads=65536,
> ΔreadMiB=256), and **DALI's numpy GPU reader DMAs sub-16 KiB `.npy` reads** (tiny 4 KiB .npy:
> Δreads=98082, ΔreadMiB=197 = GDS). So **only kvikio** applies a 16 KiB→POSIX threshold by default;
> cuFile and DALI do not. cuFile *has* the knob (`posix_gds_min_kb`) but it defaults to 0 (off). ⇒ This
> scenario demonstrates a **reader (kvikio) policy that GDS tools can't see — NOT GDS silently failing
> on its own.** The honest, ecosystem-level takeaway is the **divergence**: identical data + identical
> GDS stack, yet kvikio→POSIX while DALI/cuFile→GDS for the same small reads, and **no per-op tool tells
> you which path YOUR reader+config actually took.** That (cross-layer, *reader-aware*, per-op
> attribution) is the defensible thesis; "GDS silently falls back by itself" is **not** supported here.

## The mechanism (a real kvikio default, not a bug)
kvikio routes reads **below `KVIKIO_GDS_THRESHOLD` (default 16 KiB) to its own POSIX path**, *above*
cuFile — to avoid GDS per-op setup cost on tiny reads. Reasonable as a default; the problem is it's a
**silent policy decision invisible to every GDS observability tool**, so users can't tell it's
happening, whether it's right for their workload, or what it costs.

## The data
**Read-size sweep (kernel oracle, `ΔreadMiB/requested`):**

| read size | 4 KiB | 8 KiB | 16 KiB | 32 KiB | … | 1 MiB |
|---|---|---|---|---|---|---|
| kernel DMA | **0** | **0** | full | full | … | full |
| path | **POSIX** | **POSIX** | GDS | GDS | … | GDS |

→ **threshold = 16 KiB** (matches kvikio's documented default). ≥16 KiB reads are 1:1 with kernel
reads (no amplification when aligned).

**Pure 4 KiB run (sub-threshold), reading 1171.9 MiB:**
- `gds_stats`: **`Read ok = 0`, `Total Read Size = 0`, `posix = 0`** — the GDS tool sees *nothing*.
- kernel: `Δreads = 0`, `ΔreadMiB = 0` — no DMA (POSIX confirmed).
- actual: 1171.9 MiB read at **0.033 GiB/s** — ~**88× slower** than the ~2.9 GiB/s GDS path.

**Realistic mixed (bimodal) run — 60% small (<16K) + 40% large (≥64K) by count:**
| | small (<16K) | large (≥64K) | what tools show |
|---|---|---|---|
| reads | 39,731 (311 MiB) | 26,269 (7378 MiB) | — |
| kernel DMA (`ΔreadMiB`) | **0** | **7378** | only large reads DMA |
| `gds_stats` | invisible | counted | **`posix = 0`** ("GDS perfect"); small 60% never appears |
| aggregate throughput | ~0.5 GiB/s (dragged down) | — | low, but `gds_stats` says GDS is fine → unattributable |

## Why current tools can't observe it (the gap)
- **`gds_stats` / `cufile.log` are cuFile-layer** — kvikio's sub-threshold POSIX path **never calls
  cuFile**, so they show `Read ok=0` / `posix=0`: *blind*, and worse, *reassuring* (`posix=0` reads as
  "no fallback").
- **Throughput / `nvidia-smi`** show a low aggregate but can't attribute it to the sub-threshold reads
  vs. the disk vs. topology. (This is exactly forum user *fuyao3860*'s "which really confuses me," and
  *pandeyshweta2401*'s "is GDS actually enabled?" — see `rw-claude.md`.)
- **The kernel `nvidia-fs` counter** is the only ground truth, and only as a *negative* signal
  (DMA-bytes < bytes-read) — you must already know the true byte count from elsewhere, and it gives no
  per-op / per-file / per-layer attribution.
- **Darshan/DXT/Recorder** have no cuFile module at all.

## Why this is the right motivation (and honest framing)
Not "kvikio is broken" — for tiny reads, POSIX may genuinely be fine. The contribution is
**observability/decision-support**: today you *cannot see* that a subset of your reads took POSIX,
*which* reads, or *what it cost*, with any GDS tool. That blindness matters most for the workloads GDS
is *marketed for*: **small-random-read** pipelines — vector/embedding retrieval (ESPN), LLM KV-cache
restore (Tutti), metadata-heavy or variable-chunk (Zarr/Parquet) datasets — which sit right at/below
the 16 KiB line. A user configures GDS, `gdscheck`/`gds_stats` look clean (`posix=0`), and a large
fraction of their I/O is silently on POSIX at a fraction of the bandwidth.

## What would fix it (→ GDS-Trace)
Per-operation, cross-layer attribution at the **reader↔cuFile boundary** (GOTCHA over the full cuFile
API surface incl. async/batch — LD_PRELOAD is insufficient, see `results/step3/STEP3-FINDINGS.md`),
**reconciled with the kernel nvidia-fs counters**, emitting per-op: GDS-DMA vs POSIX, size, file/chunk,
and cost — the verdict `gds_stats` (`posix=0`) cannot give. This also subsumes the
activation/correctness verifier (`is GDS active per-op?`).

## Reproduce
```bash
/opt/gds-tools/mount_gds_nvme.sh
echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled    # kernel oracle on
# threshold sweep:
for sz in 4096 8192 16384 65536 1048576; do  # measure /proc/driver/nvidia-fs/stats Reads delta around each
  python step3/step3_sizesweep.py --size $sz --count $((256*1024*1024/sz)); done
# mixed demo (gds_stats posix=0 vs kernel DMA = large-only):
python step3/step3_mixed.py --p-small 0.6 --secs 15   # + gds_stats -p <pid> and nvidia-fs Reads delta
```

## Caveats
- Single-thread, single PM983, 4 GiB file (cold via drop_caches). Multi-thread/file≫RAM expected to
  show the same path split (it's a size-routing decision, not a contention effect).
- The 16 KiB threshold is tunable (`KVIKIO_GDS_THRESHOLD`) — but you must *know* to, and no GDS tool
  tells you you're hitting it. That unknowability is the point.
- `gds_stats posix=0` here means cuFile-layer POSIX = 0; the POSIX happens in kvikio above cuFile.
