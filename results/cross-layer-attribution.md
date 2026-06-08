# Cross-layer per-op attribution: timing/thread (corr_id) + deterministic address (LBA)

The core method problem for per-op GDS attribution: tie each NVMe command back to the causing cuFile op,
across the userspace→kernel→device boundary where cuFile's worker/async threading breaks thread-locality
and the device command carries no application identity. We measure two complementary mechanisms across
regimes (A100, CUDA 12.6, nvidia-fs 2.28; `dataset.bin` on raw `nvme1n1`; `tools/lba_match.py`).

| workload (regime) | cuFile ops | NVMe | **corr_id** (timing/thread) | **LBA** (device address) |
|---|--:|--:|--:|--:|
| kvikio 64 KiB sync, single stream | 2000 | 2004 | 99.8% | 97.0% |
| 8 threads × 64 KiB, sync | 4000 | 4004 | 99.9% | 93.2% |
| 8 threads × 4 MiB, sync (repeated access) | 800 | 3603 | 99.9% | **47.8%** |
| fastsafetensors shard load | 1 | 1819 | 99.8% | 99.8% |
| **cuFileReadAsync (200, 32 in flight)** | 200 | 203 | **6.4%** | **98.5%** |
| **cuFileReadAsync (2000, 32 in flight)** | 2000 | 2003 | **8.5%** | **97.2%** |

## Findings
- **corr_id** (per-tid + tgid-op-count fallback) is robust for **synchronous** I/O (≥99.8%), including heavy
  threading — the device command fires on the submitting thread, so per-tid resolves it. It **collapses on
  async** (`cuFileReadAsync`, 6–8%): cuFile drives the I/O internally, fully decoupled from the submitter,
  and the NVMe command carries *no* corr_id. Worse, of the few it does assign on async, only ~37% are
  correct — so it's not just incomplete, it's unreliable there.
- **LBA** (map each cuFile op's file range → device LBAs via the file extent map; match each NVMe sector
  back) is robust for **async/thread-decoupled** I/O (97–98%) because it ignores threads and timing
  entirely. Its failure mode is **same-range ambiguity** — two ops reading the identical file bytes are
  indistinguishable by address (47.8% on large-reads-over-a-small-file, where random collisions dominate);
  timing disambiguates exactly those.
- **They are complementary, and where both apply they agree** (≈100% on fastsafetensors; 97% on kvikio) —
  so the cheap heuristic is *validated correct*, not merely plausible, by an independent deterministic method.

## Contribution (method/design)
A cross-layer attribution **framework** that pairs a lightweight timing/thread heuristic (`corr_id` — cheap,
always-on, robust for synchronous I/O) with a deterministic device-address matcher (LBA — robust for
async/worker-pool-decoupled I/O), each covering the other's failure mode, mutually validating where they
overlap. In particular, **per-op attribution of *asynchronous* cuFile I/O below the cuFile API** — the
regime inference stacks (batched weight load, KV transfer) increasingly use, and where `gds_stats`/Nsight
and a timing heuristic alone are blind — is, to our knowledge, not done by any prior tool.

Notes: async GDS tracing works once the tracer is clean — an earlier "async segfault" was a stale-server
pileup (multiple datacrumbs servers colliding on the same eBPF probes), not the async path. Reproduce:
`workloads/async_reader.py` + `tools/lba_match.py`.
