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

\* run-to-run variance (see drift notes).

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
