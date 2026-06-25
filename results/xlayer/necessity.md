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

**Standing:** the *necessity mechanism* is proven; turning it into a *defensible paper finding* needs
a real (non-constructed) heterogeneous instance, or honest framing as a methodology/capability result
with this controlled demonstration. Tools: `workloads/gds_mixed.c`, `tools/mixed_score.py`.
