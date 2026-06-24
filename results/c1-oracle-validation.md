# C1 method validation against an INDEPENDENT per-op oracle (2026-06-24)

Fixes the audit's #1 finding: the prior corr_id/LBA "accuracy" was **self-/mutual-consistency**
(corr_id coverage + the two methods agreeing), never scored against ground truth. Here we build a
real oracle and score both methods against it.

## The oracle (independent of corr_id AND of the tracer's timing)

Workload `workloads/gds_oracle.c` issues cuFile reads over **unique, non-overlapping slots**
(slot *i* = file_offset *i·S*, read exactly once). A device command's physical **LBA → file_offset
(via the file extent map) → slot** is therefore the *true* causing op — a ground truth derived from
**address layout**, using neither corr_id nor timing. Modes: `threads T` (T pthreads, synchronous
cuFileRead), `async IF` (cuFileReadAsync, IF in flight; optional `HOT` = #distinct slots → overlap).
Scorer: `tools/oracle_score.py` (reuses the `lba_match` extent machinery). 4000 reads × 64 KiB,
single PM983.

## Result

| regime | corr_id correct vs oracle | LBA uniquely attributable |
|---|--:|--:|
| sync (T=1) | **100.0%** | 100.0% |
| **concurrent sync (T=16)** | **100.0%** | 100.0% |
| async (64 in flight, distinct ranges) | **1.6%** | 100.0% |
| async (128 in flight) | **0.8%** | 100.0% |
| **async + overlapping ranges (hot=64)** | 1.6% | **0.0%** |

## What it establishes (honest, both ways)

1. **corr_id is exact under synchronous I/O — including *concurrent* sync (16 threads, 100%).** This
   *corrects* the project's earlier framing that thread-concurrency degrades corr_id: it does **not**.
   The degradation is specific to **async** (cuFileReadAsync), where submit and device completion
   decouple across contexts → the kernel probe reads a stale/foreign thread's corr_id → **collapse to
   ~1%** (worse than the previously-cited 6–8%; now measured vs ground truth, not self-coverage).
2. **LBA recovers the async regime (100%)** *when op ranges are distinct* — address correlation is
   timing-independent. This is now validated against an oracle, not asserted.
3. **LBA's real blind spot, measured:** under **overlapping ranges** (many ops read the same slot),
   LBA can attribute the *data region* but **cannot pin which op (0% uniquely attributable)**. So the
   honest method statement is: *corr_id for sync, LBA for async-with-distinct-ranges, and **neither**
   for async-with-overlapping-ranges* — the last is a genuine, now-characterized limitation (the
   regime the audit flagged as never-tested).

## Paper wording (replaces the mislabeled "99.8% accuracy validated vs oracle")

> Scored against an independent address-layout oracle: corr_id attributes synchronous I/O exactly
> (100%, incl. 16-way concurrent) but collapses under async decoupling (≤1.6%); LBA attributes async
> I/O exactly for distinct ranges (100%) but cannot disambiguate overlapping ranges (0% unique).
> The two are complementary; async + overlapping ranges is an open limitation.

Tools: `workloads/gds_oracle.c`, `tools/oracle_score.py`; data `results/xlayer/oracle.csv`.
