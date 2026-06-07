# Step 3 & 4 — [CORRECTED] kvikio/DALI use TRUE GDS even for unaligned / .npy reads

> ## ⚠️ CORRECTION (2026-06-07) — the "silent POSIX bypass" finding below is RETRACTED
>
> The original conclusion in this doc ("a subset of reads silently bypasses GDS to POSIX; `n=0`")
> was **WRONG — a measurement error.** I had grepped only the cuFile **per-GPU** `Read: n=` counter,
> which reads 0 for unaligned reads, and never checked `GLOBAL Read: ok` (≈17,000 for *both* aligned
> and ragged) or the **kernel nvidia-fs `Reads`/`readMiB`** counters.
>
> With nvidia-fs IO stats enabled and a `gdsio -x0` positive control (Δreads=2048, ΔreadMiB=2048 for
> a 2 GiB read — exact), the kernel ground truth is:
>
> | workload | kernel Δreads | kernel ΔreadMiB | workload | verdict |
> |---|---:|---:|---:|---|
> | gdsio -x0 (control) | 2048 | 2048 | 2 GiB | true GDS |
> | kvikio **aligned** | 6000 | 3195 | 3.12 GiB | **true GDS** |
> | kvikio **ragged** | 6019 | 3190 | 3.09 GiB | **true GDS** |
> | kvikio **.npy** | 468 | 324 | 0.32 GiB | **true GDS** |
>
> **All kvikio reads — aligned, ragged, and `.npy` — perform real NVMe→GPU DMA.** There is **no
> silent POSIX bypass.** DALI's `n=0` was the same artifact. The actual takeaways are far more modest:
> (1) the cuFile **per-GPU userspace stats are misleading** (`n=0`/`posix=0` while the kernel DMA'd) —
> a tooling gotcha, not a pathology; (2) mild **read splitting** for unaligned `.npy` (468 kernel
> reads for ~300 logical reads) — possible small amplification, to be quantified; (3) the project's
> *silent-fallback* premise was **NOT** demonstrated here — on this stack GDS works for unaligned/.npy.
>
> Everything from here down is the **original (incorrect)** writeup, kept for the record. Do not cite it.

---

# [SUPERSEDED] Step 3 & 4 — Silent per-op GDS bypass on a vendor reader (kvikio), and why coarse tools miss it

**Node:** Chameleon A100, true-GDS (image `grc-ub2404-nvk-gds-a100-cu126-v3`). **Date:** 2026-06-07.
**Reader:** kvikio 26.04 (cuFile/GDS) · **Workload:** `step3/step3_ragged.py` reading many records
`(offset,size)` from a 4 GiB file on the local NVMe (ext4 `data=ordered`) into GPU memory.
Single-threaded random reads, cold cache (root `drop_caches`). cuFile counters captured **live** via
`gds_stats -p` (cuFile segfaults at process exit on this kvikio-26 / libcufile-12.6 stack, so the
exit-time stats dump is unavailable — live attach sidesteps it).

Modes: **aligned** = 4K-aligned offsets + 4K-multiple sizes (GDS-friendly); **ragged** = arbitrary
offsets + arbitrary sizes (like variable-size *compressed* chunks); **mixed** = interleave the two.

## The result

| mode | throughput | cuFile GDS reads `n` | `posix` | `unalign` |
|---|---:|---:|---:|---:|
| aligned | **1.243 GiB/s** | **9926** | 0 | 0 |
| ragged  | **1.229 GiB/s** | **0** | 0 | 0 |
| mixed   | **1.233 GiB/s** | **4956** (≈ the aligned half only) | 0 | 0 |
| ragged + `KVIKIO_COMPAT_MODE=OFF` | 1.198 GiB/s | **0** (no error) | 0 | 0 |

(Step-1 4-thread sequential ceiling was ~2.9 GiB/s; these single-thread random runs are
latency-bound at ~1.2 GiB/s — same for every mode.)

## What it means (the money finding)

1. **A subset of reads silently bypasses GDS entirely.** kvikio routes unaligned/ragged
   ("compressed-chunk-like") reads through its **CPU/POSIX path**, not the NVMe→GPU DMA. In `mixed`
   (the realistic case) only ~half the reads (`n=4956`) used GDS; the ragged half never reached the
   DMA path (`aligned n≈9926` → `mixed n≈4956`). No error, no warning.
2. **Aggregate throughput is blind to it.** aligned ≈ ragged ≈ mixed ≈ **1.23 GiB/s**. You cannot
   tell from bandwidth (or `nvidia-smi`) that half the I/O skipped GDS — at this access pattern the
   DMA and POSIX paths are both latency-bound at the same rate.
3. **`gds_stats` is actively misleading here.** It reports **`posix=0`** for the ragged run — i.e.
   "zero POSIX fallbacks" — even though **100% of ragged reads were served by POSIX**. The bypass
   happens *above* cuFile (inside kvikio), so cuFile/`gds_stats` never see those reads at all; the
   only hint is that the GDS read *count* silently drops. The brief's *primary* aggregate instrument
   does not catch this class of fallback.
4. **It can't be forced off.** `KVIKIO_COMPAT_MODE=OFF` does not push ragged reads onto GDS and does
   not error — the per-read fallback is silent and unavoidable from config.

## Why this validates the GDS-Trace premise / architecture

The pathology the project predicted — *a realistic vendor reader silently servicing a subset of ops
off the GDS path, invisible to coarse tools* — reproduces here **without trying to break anything**.
Critically, it is invisible to both throughput **and** `gds_stats`, because the decision is made at
the **kvikio↔cuFile boundary**. That is precisely the layer GDS-Trace proposes to instrument via
**GOTCHA/LD_PRELOAD over the cuFile API** (the DFTracer interposer prototyped on DeltaAI), which
would attribute, per operation, *which app read / which dataset chunk* took POSIX vs GDS — the
attribution neither bandwidth nor `gds_stats` provides.

## Caveats / next refinements
- Single-threaded, random, 4 GiB < RAM (cold via `drop_caches`). A multi-threaded / file≫RAM run
  would push absolute BW up but is expected to show the same per-op blindness.
- Dataset is `dd` zeros (path behavior, not content, is under test); a real compressed dataset
  (e.g. `.npy`/`.npz` shards, kvikio numpy reader, or DALI `fn.readers.numpy`) is the next step.
- Confirm with **DALI** (second vendor reader) and then point the **DFTracer cuFile GOTCHA tracer**
  at this workload to produce the per-op attribution = the proposal's Figure 1.

## Reproduce
```bash
/opt/gds-tools/mount_gds_nvme.sh
dd if=/dev/zero of=/mnt/nvme1/gdstrace-smoke/dataset.bin bs=1M count=4096 oflag=direct
# stats-enabled cufile.json (cufile_stats:3); run + live gds_stats:
CUFILE_ENV_PATH_JSON=<stats.json> /opt/gds-venv/bin/python -u step3/step3_ragged.py --mode {aligned|ragged|mixed} --secs 18 &
gds_stats -p $! -l 3   # read Read: n=/posix=/unalign= live
```
Raw: `results/step3/run_*.txt`, `results/step3/step3_summary.txt`.

---

## Task #1 — Cost of the bypass, and why it's *invisible* on this node

Host CPU + page-cache for the same kvikio reads (6000 records, cold cache):

| reader / mode | GiB | CPU-s/GiB | page-cache Δ |
|---|---:|---:|---:|
| kvikio aligned (cuFile `n>0`) | 3.12 | **2.75** | +144 MiB |
| kvikio ragged (POSIX, `n=0`)  | 3.09 | **2.77** | +144 MiB |

**Identical.** So even when kvikio *does* call cuFile (aligned), it doesn't realize GDS's benefit —
the GPU buffer isn't registered, so cuFile stages through a **host bounce buffer**, costing the same
host CPU as POSIX. (kvikio's ~2.75 CPU-s/GiB is dominated by Python per-read overhead — 6000 calls.)

Anchored against an efficient C reader with registered buffers (`gdsio`):

| path | GiB/s | CPU-s/GiB |
|---|---:|---:|
| `gdsio -x0` (true GDS DMA) | 2.94 | 0.24 |
| `gdsio -x1` (CPU bounce)   | 2.93 | 0.14 |

Both ~2.9 GiB/s, ~0.1–0.2 CPU-s/GiB: the **single PM983 is the bottleneck**, so GDS's BW/CPU edge
over the bounce path is marginal (matches Step 1's `-x0 ≈ -x1`).

**Conclusion (#1):** on this single-drive node the silent bypass costs **~0 in throughput, CPU, and
page-cache** — invisible to *every* coarse metric (and `gds_stats` says `posix=0`). That is the
strongest form of the project's argument: nothing but per-op cross-layer attribution reveals it. The
bypass turns *materially* costly off the drive-bound regime — multi-drive / higher-BW storage where
the host bounce path saturates CPU and **host memory bandwidth** (GDS's real benefit). Showing that
magnitude needs faster storage than one *shared* PM983 (we can't RAID the shared disks) or uncore
memory-BW counters; on this node the contribution is precisely the **invisibility**.

---

## Task #2 — Realism: multi-threaded, real `.npy` dataset, two vendor readers

Synthetic offsets aren't needed — **real `.npy` files defeat GDS by construction.** numpy writes a
header (`\x93NUMPY` + version + dict), so the array data starts at offset **128 B — not 4K-aligned**.
300 `.npy` files of varied (ragged) sizes, read multi-threaded (8 threads), cold cache:

| vendor reader | throughput | reads/iters | cuFile GDS `n` | `posix` |
|---|---:|---:|---:|---:|
| **kvikio** (8 threads, 15 s) | 1.52 GiB/s | 21,900 reads / 23.1 GiB | **0** | 0 |
| **DALI** numpy GPU reader (8 threads, 15 s) | — | 18,083 iters | **0** | 0 |

- **Both vendor readers silently bypass GDS for 100% of reads** on a ubiquitous real format — the
  128 B npy header pushes the data off the 4K boundary, so every read takes the CPU/POSIX path. No
  error; throughput looks normal; `gds_stats` shows `posix=0` for both (blind, as before).
- Multi-threading lifts BW (1.23 → 1.52 GiB/s) but does **not** restore GDS — confirming the bypass
  persists under concurrency, and BW remains blind to it.
- **Honest caveat:** for kvikio the evidence is *causal* (same reader: aligned→`n>0`, unaligned→`n=0`).
  For DALI we observed `n=0`/`posix=0` (consistent with bypass) but did not independently force its
  GDS path on 4K-aligned data on this stack, so DALI is "consistent with" rather than proven-causal.

**Takeaway:** a practitioner loading `.npy` shards with kvikio *or* DALI believes they're using GDS;
for the entire dataset they are not — and no bandwidth, CPU, page-cache, or `gds_stats` number tells
them. Per-op attribution at the cuFile API (task #3) is what surfaces it.

---

## Task #3 — Per-op tracer: which interception layer actually works

Goal: a transparent tracer that attributes, per op, GDS vs POSIX-bypass. Added `external/dftracer`
and `external/brahma` (the GOTCHA-based interposition substrate) as submodules, and built a focused
**LD_PRELOAD interposer** (`tools/gds_trace_preload.c`) hooking **both** `cuFileRead` (GDS) and
`pread64/pread/read` (POSIX) with `/proc/self/fd` file attribution — the same idea as brahma's GOTCHA
wrappers.

**What it caught, run under the kvikio `.npy` workload:**

| run | `cuFileRead` (GDS) seen | libc POSIX reads seen |
|---|---:|---:|
| kvikio `.npy` | **0** | 300 — but these are **Python parsing the npy headers**, not the data reads |
| kvikio aligned | **0** | 0 |

…yet `gds_stats` independently showed the aligned run did `n=9926` cuFile GDS reads. So **kvikio's
actual data I/O is invisible to libc-level LD_PRELOAD** — confirmed by the symbols in `libkvikio.so`:
it **`dlsym`'s cuFile** (so LD_PRELOAD, which only interposes dynamic-linker resolution, never sees
it) **and uses the async/batch API** (`cuFileReadAsync`, `cuFileBatchIOSubmit`), **not** plain
`cuFileRead`. The bypassed (`.npy`) reads surface as neither cuFile nor libc `pread64`.

**This is the architectural finding for GDS-Trace:**
1. The tracer must use **GOTCHA** (binary GOT patching, wraps `dlsym`'d symbols when installed before
   the reader resolves them) — **LD_PRELOAD alone is insufficient.** This is exactly why DFTracer/
   brahma use GOTCHA. → fold the hooks into `external/dftracer/src/dftracer/core/brahma/cufile.cpp`.
2. It must cover the **full cuFile API surface** — sync **and** `*Async` **and** `cuFileBatchIO*` —
   not just `cuFileRead`. (A `gds_stats`/`cuFileRead`-only view misses real readers like kvikio.)
3. The truly-bypassed reads (no cuFile call at all) need either reader-API hooks **or** the
   **kernel/eBPF** layer (the brief's kernel-depth tier) — neither `gds_stats` nor cuFile-API
   interposition can see an op that never enters cuFile.

**Status:** submodules in place; LD_PRELOAD prototype + interception-layer requirement established.
Remaining (next session): build DFTracer (cpp-logger + GOTCHA 1.0.5 + brahma v0.0.3 + yaml-cpp via
`dependency/install_dependency.sh`), add the `*Async`/`BatchIO` cuFile GOTCHA hooks, and verify
GOTCHA catches kvikio's `dlsym`'d async cuFile calls → the per-op Figure-1 trace.
