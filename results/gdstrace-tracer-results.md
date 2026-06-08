# GDS-Trace tracer — validated end-to-end results + cross-checks

The working tracer: **DataCrumbs** (eBPF; cuFile uprobe plugin sync+async+batch with per-op
size/offset/count, NVMe `nvme_setup_cmd` kprobe with size/sector, TGID worker-thread fix) →
**DFTracer `.pfw.gz`** → **DFAnalyzer** (datacrumbs/stack preset; nests `nvme_setup_cmd` under the
`cuFileRead` that issued it) → per-op cross-layer attribution + amplification. Driver:
`tools/dfa_drive.py`. Build/how-to: `DATACRUMBS-GDS-BUILD-LOG.md`.

## End-to-end results (real + synthetic readers through the full stack)
| reader / workload | cuFile API | cuFile ops | NVMe cmds | **device-cmd amplification** | bytes (cuFile = NVMe) | notes |
|---|---|---:|---:|---|---|---|
| **gdsio** `-i 1M` aligned | cuFileRead | 256 | 256 | **1.0×** | 256 = 256 MiB | baseline (≤MDTS) |
| **gdsio** `-i 4M` | cuFileRead | 64 | 256–375* | **~4–6×** | 256 = 256 MiB | 4 MiB reads split at ~1.25 MB MDTS |
| **gdsio** `-x 5` async | cuFileReadAsync | 128 | — | — | size=1 MiB via `size_t*` deref | async arg capture |
| **gdsio** `-x 6` batch | cuFileBatchIOSubmit | 125 | 128 | — | `count`+`size` from `CUfileIOParams_t` | batch arg-walk |
| **kvikio** (`step3_ragged`) | cuFileRead (sync, dlsym'd, worker-thread) | 3000 | 3013 | **1.0×** | 1601 = 1601 MiB | ragged ~546 KiB < MDTS → 1 cmd each |
| **DALI** numpy GPU reader | cuFileRead (sync) | 9137 | 22568 | **2.47×** | 9875 ≈ 9896 MiB | whole-file ~1–1.8 MB > MDTS → 2–3 cmds; **re-registers a handle per read**; also 13k `pread64` (POSIX header reads), 300 of which hit NVMe |
| **ESPN** `cufile_bread` (batch) | cuFileBatchIOSubmit | 32 submits (128 sub-ops each) | 4096 | 128 cmds/submit (1:1 per sub-op, 4 KiB) | 16 = 16 MiB | batch arg-walk gives `count=128,size=512KiB`/submit; all 4096 NVMe → root cuFileBatchIOSubmit; **4096 `cuFileHandleRegister` (0.115 s) > batch reads (0.053 s)** |

\* run-to-run variance (see drift notes).

**Amplification is a size-dependent *device* effect, not reader-specific** (NVMe MDTS ~1.25 MiB splits
larger reads): kvikio ragged (~546 KiB, <MDTS) = **1.0×**, but kvikio 4 MiB = **4.05×** (300 cuFileRead
→ 1214 NVMe), DALI whole-file = 2.47×, gdsio -i4M = ~4×. GDS-Trace surfaces it per-op for any reader.

**What no NVIDIA tool shows here:** per-op `cuFileRead{size,offset}` → the exact set of NVMe commands
it became (with sizes), the device-command amplification, kvikio's dlsym'd/worker-thread reads, and
DALI's per-read handle-registration + GDS/POSIX mix. `gds_stats` is aggregate cuFile-only; NVTX stops
at cuFile; Darshan has no cuFile.

## Cross-check vs ground-truth tools (the rigor) — gdsio `-i 4M -s 256M -x 0`
**Same run**, three independent measurements:
| layer | GDS-Trace | kernel `/proc/driver/nvidia-fs/stats` | bpftrace (independent kprobe) |
|---|---:|---:|---:|
| cuFile reads | cuFileRead = **64** | Reads `n` = **64** | — |
| cuFile bytes | **256 MiB** | readMiB = **256** | — |
| NVMe commands | nvme_setup_cmd = **259** | — | nvme_setup_cmd = **259** |

→ **cuFile layer exact** (64 = 64, 256 MiB = 256 MiB); **NVMe layer exact, same-run** (259 = 259). The
tool's per-op capture matches the kernel oracle and an independent kprobe with **zero drift in a single
run**. kvikio cross-checked the same way: cuFileRead 3000 ≈ kernel Reads `n`, bytes 1601 MiB ≈ readMiB.

## Pathologies GDS-Trace diagnoses that coarse tools miss
Each is a per-op cross-layer effect invisible to `gds_stats` (aggregate, cuFile-only), `nvidia-smi`
(PCIe only), throughput, or Darshan (no cuFile) — but GDS-Trace pinpoints it per op.

1. **kvikio sub-16 KiB path divergence (silent POSIX).** Bimodal kvikio workload (60% reads <16 KiB):
   GDS-Trace shows **13529 `cuFileRead` (GDS, sizes ≥64 KiB) + 20472 `pread64` (POSIX, the small
   reads kvikio routed off GDS, ~166 MiB)**. Cross-check, same class of run: **`gds_stats` reports
   `posix=0`** ("GDS perfect") and the kernel `nvidia-fs Reads n` counts **only the large reads** — i.e.
   60% of reads silently took POSIX and no GDS tool shows it; GDS-Trace shows *which* reads and how much.
2. **DALI device-command amplification.** DALI numpy GPU reader: **9137 `cuFileRead` → 22568
   `nvme_setup_cmd` = 2.47×** (whole-file ~1–1.8 MB reads split at the ~1.25 MB MDTS into 2–3 commands),
   plus a **`cuFileHandleRegister` per read** (9137 — handle-reg overhead) and a `cuFileRead`(GDS)/
   `pread64`(POSIX header) mix. `gds_stats` shows aggregate GDS bandwidth (looks fine); GDS-Trace shows
   the per-op IOPS cost + the handle churn.
3. **Unaligned byte amplification.** gdsio 64 KiB random read, aligned vs `-U` unaligned: op count
   identical, but device **bytes go 128 MiB → 136 MiB = 1.06× byte amplification** (cuFile reads the
   4 KiB-aligned superset). Still reported as `GPUD` (GDS), so `gds_stats`/throughput look clean;
   GDS-Trace shows cuFileRead requested 128 MiB while NVMe moved 136 MiB — 6% wasted device bandwidth.
4. **Redundant per-read handle registration (ESPN).** ESPN's batch reader registers a `cuFileHandle`
   **per read** (4096 `cuFileHandleRegister` for 4096 reads of one file) — GDS-Trace shows it spends
   **more wall-time registering handles (0.115 s) than on the batch reads themselves (0.053 s)**. A
   diagnosable GDS-usage inefficiency (register once, not per op) that `gds_stats` (no per-op
   HandleRegister timing) cannot surface.

**Why this is the contribution:** these are exactly the "is GDS actually doing what I think, per op?"
questions from the demand evidence (forum users `fuyao3860`/`pandeyshweta2401`; Muradli's hand-rolled
timers). GDS-Trace answers them with per-op, cross-layer ground truth that no shipped tool provides.

## Drift notes (why numbers differ where they do)
1. **NVMe-command count varies run-to-run** (256 / 259 / 375 for identical gdsio `-i4M`). Cause:
   device-side splitting of >MDTS (~1.25 MB) reads is non-deterministic (MDTS, queue state, alignment).
   So *amplification is a per-run measurement, not a fixed constant* — but within any single run the
   tool matches bpftrace exactly. Compare tool↔oracle **within the same run**, never across runs.
2. **~0.3% of `nvme_setup_cmd` fire in non-process (softirq/kworker) context** (bpftrace saw 376 total
   vs 375 in `gdsio`), which the TGID filter does not attribute — a negligible undercount, and those
   completions aren't attributable to a process op anyway.
3. **`iostat` (device-wide) ≥ GDS-Trace (process-scoped):** iostat counts all I/O incl. other tenants/
   background; GDS-Trace counts only the traced process — by design.
4. **`gds_stats` per-GPU `n` is misleading** (showed 0 while the kernel DMA'd, see STEP3-FINDINGS) —
   trust GLOBAL `Read: ok` / kernel counters; GDS-Trace matches those.
