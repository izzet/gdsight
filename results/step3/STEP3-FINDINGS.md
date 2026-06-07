# Step 3 & 4 — Silent per-op GDS bypass on a vendor reader (kvikio), and why coarse tools miss it

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
