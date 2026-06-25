# Necessity test: when is per-op cross-layer attribution a *must*? (2026-06-25)

Goal (per the "intellectually-defensible + method-must-be-necessary" bar): find a regime where the
per-op cross-layer method is the **only** way to obtain a true, non-obvious result. Heterogeneous
*async* workloads are that regime. Controlled demo via `workloads/gds_mixed.c` (two op-classes,
scattered so reads don't merge): A = 200×1 MiB 4K-aligned; B = 2000×3 KiB at +3072 misalign (spans 2
blocks). Score: `tools/mixed_score.py`.

## Result — three observers, one truth

| observer | class A | class B | verdict |
|---|--:|--:|---|
| **(1) aggregate** device/app (iostat-vs-app) | — | — | **1.047× → looks BENIGN** (blind) |
| **(2) corr_id** (timing) per-class | 0.635× | 2.215× | **WRONG** (A<1 is impossible; 36% of cmds unresolvable; under-counts B 17%, mis-bills to A) |
| **(3) LBA** (address) per-class | 1.000× | **2.667×** | **CORRECT** (100% of 2200 cmds classified) |

Class B is **3% of bytes but ~91% of ops**, each wasting 63% of its device bandwidth — and the
aggregate ratio (1.05×) hides it completely.

## The necessity chain (airtight, with honest scope)

1. aggregate cannot localize per class (information lost in the average) — and you **cannot isolate
   the class** in a real mixed run to measure it separately;
2. the timing method (corr_id) gives **wrong** per-class numbers under async decoupling;
3. ⇒ **address-based per-op cross-layer attribution (LBA) is the only observer that yields the true
   per-class device amplification.** This makes both the eBPF cross-layer capture *and* the LBA
   algorithm necessary, not merely convenient.

## Honest assessment against the bar (do NOT oversell)

- **Necessity: PASS.** No aggregate tool, and not corr_id, can produce the correct per-class number;
  only LBA per-op can. Scope caveat: necessity requires a *non-isolable, mixed, async* workload.
- **Non-obvious: BORDERLINE.** "Averaging hides outliers" is semi-intuitive; the defensible,
  non-trivial parts are (a) the *quantified* GDS monitoring blind spot (trusted gds_stats/iostat say
  "healthy" while 91% of ops amplify 2.7×) and (b) corr_id returning an *impossible* per-class number
  (A=0.635×) — a concrete failure, not hand-waving.
- **Constructed-to-win risk: PRESENT.** This is a *synthetic* mix; it proves the **mechanism**, not a
  real pathology. To clear the cuCIM-lesson bar it needs a **real** mixed async GDS workload
  (RAG/inference: large weight reads + small KV/embedding gather) — which has tooling obstacles here
  (python+kvikio segfault under datacrumbs_wrap; frameworks not installed).

**Standing (controlled demo):** the *necessity mechanism* is proven; turning it into a *defensible
paper finding* needs a real (non-constructed) heterogeneous instance. → done next.

---

# Result 2 — REAL kvikio RAG-mix, default config (the defensible keystone) ✅

Unlocked by rebuilding the tracer with `DATACRUMBS_TRACE_ALL_PROCESSES=ON` → the eBPF probes capture
**unmodified** processes with no injection (no `datacrumbs_track`/`wrap`, which segfaulted on the
kvikio/cupy python stack). Confirms kvikio uses the *system* libcufile we uprobe. `workloads/rag_mixed.py`
drives the **real kvikio library**: class A = 200×1 MiB shard reads, class B = 2000×3 KiB scattered
embedding/KV reads (a recognized RAG/inference pattern), 8 threads, default kvikio config. Scored
`tools/mixed_score.py` (other-process noise filtered out automatically by the ovh.dat extent map).

**The four observers on the real workload:**
- **`gds_stats`/cuFileGetStats:** sees **200 cuFileRead, all class A → "GDS healthy."** The 2000
  small reads are **below kvikio's 16 KiB threshold → silently POSIX**, so they never enter cuFile and
  are *completely invisible* to gds_stats.
- **(1) aggregate `iostat`-vs-app = 1.047×** → looks **benign**.
- **(2) corr_id on class B: 0% correct; 94% MIS-billed to class A** (the POSIX reads carry a stale
  per-thread corr_id from an earlier class-A cuFileRead), 6% unattributed → corr_id is *actively
  wrong*, hiding B's cost inside class A.
- **(3) LBA on class B: 100% attributable → class B amplifies 2.67× at the device** (15.6 MiB device
  for 5.9 MiB requested) vs class A's 1.00×.

**Why it clears the bar:**
- **Non-obvious:** the workload's own GDS health tool reports *perfectly healthy* while 91% of its
  operations (a) silently bypass GDS *and* (b) waste 63% of their device bandwidth — and the
  timing-based attributor *mis-bills that waste to the clean class*. Three different "trusted" views
  all mislead, each in a different way.
- **Method provably necessary:** gds_stats blind (not cuFile) · iostat blind (aggregate) · corr_id
  wrong (0% correct, 94% mis-billed) ⇒ **address-based per-op cross-layer attribution (LBA) is the
  ONLY observer that recovers the truth.** Both the eBPF cross-layer capture and the LBA algorithm are
  necessary, not convenient.
- **Not constructed-to-win:** real kvikio, **default** config; the bypass is kvikio's actual behavior,
  not forced. (Honest: the A/B mix ratio is a representative RAG pattern we chose, not a captured
  production trace.)

Tracer modes: **pid-filter** (default, low-overhead, targeted via `datacrumbs_track`) vs
**trace-all** (capture unmodified processes; rebuild flag). Tools: `workloads/rag_mixed.py`,
`tools/mixed_score.py`; controlled C analog `workloads/gds_mixed.c`.
