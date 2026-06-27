# GDS observability: what each signal costs to get, and what GDSight adds

For each piece of data you'd want about a GPUDirect Storage workload: the best **existing** way to get it
(and at what granularity / when), what **GDSight** gives, and the **honest net difference**. Grounded
in this repo's measurements (gdsio / kvikio / DALI / ESPN / fastsafetensors / LMCache / embedding-gather).

Legend for "when/granularity": **agg** = aggregate counter (per-process or device-wide, runtime gauge);
**per-op** = one record per I/O op; **manual** = requires correlating two tools/counters yourself;
**userspace-only** = stops at the cuFile API (cannot see nvidia-fs / NVMe).

## cuFile-layer signals (userspace) — existing tools already do most of this per-op
| Data | Best existing tool (when/granularity) | GDSight | Difference |
|---|---|---|---|
| cuFile op count + requested size/offset | `cuFileGetStats`/`gds_stats` (**agg**); NVTX→Nsight Systems (**per-op**, userspace, offline) | per-op size/offset/count (read/write/async/batch) | ~none vs NVTX (both per-op). vs `gds_stats`: per-op not agg |
| Per-op cuFile latency | NVTX→Nsight (**per-op**, userspace); `/proc` Avg-Latency (**agg**) | per-op `cuFileRead` duration | ~none vs NVTX |
| Handle-registration churn (register-per-op) | NVTX (**per-op** timeline); `gds_stats` BufRegister (**agg**) | per-op `cuFileHandleRegister` count+duration vs the I/O | ~none vs NVTX; we quantify reg-time ÷ io-time |
| Concurrency / overlap of ops | NVTX→Nsight timeline (**per-op**, visual) | overlap factor (`get_job_time`) | ~none; we give a number vs a picture |
| GDS used vs POSIX/compat **fallback** | `gds_stats` posix counter (**agg**); `cufile.log` TRACE (**per-op**, text log) | per-op: `cuFileRead` **with** `nvfs_io` (GDS) vs **without** → `pread64` (compat) | per-op attribution of *which* ops fell back (log gives it only if TRACE on) |

## Below-cuFile signals (kernel + device) — this is where GDSight is unique
NVIDIA states GDS profiling is **"not supported for nvidia-fs.ko"**, so NVTX/Nsight **stop at cuFile**.
Below that you only have aggregate or device-wide counters with **no link to the causing op**.
| Data | Best existing tool (when/granularity) | GDSight | Difference |
|---|---|---|---|
| True zero-copy **P2P vs host-bounce** (per op) | nvidia-fs `/proc` "active shadow-buffer" (**agg** gauge) | per-op `nvfs_get_p2p_dma_mapping` vs `nvfs_mgroup_pin_shadow_pages` | **NEW: per-op verdict** (was only an aggregate gauge) |
| NVMe device-command count (**amplification**) | `iostat`/`/proc/diskstats`/blktrace (**device-wide agg / manual**) | per cuFile op → the N `nvme_setup_cmd` it became | **NEW: attributed to the causing op** |
| **Device bytes moved vs requested** (byte amplification = wasted BW) | requested from `gds_stats` (cuFile **agg**) **+** device from nvidia-fs `readMiB` or `iostat` (**agg**) → you **manually diff two layers** | per-op `cuFileRead.size` vs `Σ nvme.size` (corr_id) → exact ratio | **NEW: per-op** (e.g. 2.000× on the 3072 B gather); existing path = manual, aggregate-only |
| **cuFile op ⇄ its NVMe commands** (cross-layer attribution) | **none** — must hand-correlate by timestamp (breaks under threads/async) | corr_id carried cuFile→nvidia-fs→NVMe (99.9% single/seq; 98.5% gdsio) | **NEW core capability: no shipped tool does this** |
| nvidia-fs op present (cuFile↔driver bridge) | none per-op (`/proc` agg only) | per-op `nvfs_io` | **NEW: per-op** |
| Host **CPU** in the submit path (cost of amplification) | `perf`/flamegraph (**not op-attributed**) | not yet (nvfs_io/nvme are point events) | gap — would need durations; matters only on faster/IOPS-bound storage |

## Bottom line
- **cuFile-layer (userspace):** existing tools — chiefly **NVTX→Nsight Systems** (per-op) and
  `gds_stats` (aggregate) — already cover op size/latency/handle-reg/concurrency. GDSight ≈ matches
  them; **little net difference** there.
- **Below cuFile (nvidia-fs ↔ NVMe):** NVTX cannot follow (not supported for `nvidia-fs.ko`), and the
  kernel/device counters are **aggregate or device-wide with no op linkage**. GDSight's **per-op
  cross-layer attribution (corr_id)** is the genuinely new capability, and everything valuable it
  surfaces — device-command amplification, **byte amplification / wasted bandwidth**, true-P2P-vs-bounce,
  silent compat fallback — follows from being able to tie a kernel/NVMe event back to the cuFile op.
- **Honest caveat:** the *effects* themselves (e.g. unaligned 4 KiB-block byte amplification) are mostly
  known; the difference is **automatic, per-op attribution** of them, where today you'd manually diff two
  aggregate counters across two layers and still never learn *which* op.
