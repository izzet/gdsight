# Claims audit (2026-06-24) — adversarial pre-paper verification

Triggered by the cuCIM correction (production `read_region` coalesces / is clean; the "82% bypass"
is only the *naive per-tile* path). We audited every PDSW headline claim the same way. Five parallel
read-only audits over `results/`, `workloads/`, `tools/`, and the build-log/git history.

**Cross-cutting finding:** the repo's *raw measurements are largely real and impressively
self-corrected* (many honest tempering commits). The problem is that **`PDSW26-PAPER-PLAN.md`'s
headlines did not inherit the tempering** — they still read like the pre-correction story. cuCIM was
the most egregious case, not the only one. Separately, the **core method's "accuracy" is mislabeled**
(self-consistency, not oracle-validated). This is a framing/validation problem, **not** fabrication.

## Verdict table

| Claim | Verdict | The asterisk (what a reviewer catches) |
|---|---|---|
| **C2 — tool-blindness / who-sees-what** | ✅ **SOLID** | Stress-tested AND self-corrected (commit 5c03e16 grants iostat/iotop/nvidia-fs aggregate detection). Defensible *iff* the "per-op" qualifier always stays attached. The strongest contribution. |
| **C1 — corr_id + LBA method** | ⚠️ **NEEDS-TEMPERING** (backbone) | The "99.8% accuracy validated vs kernel/bpftrace oracle" is **mislabeled**: `lba_match.py` computes each method's *self-coverage* (`cid in corr_ids`; `len(hits)==1`) and cross-checks the two methods *against each other*; the bpftrace/nvidia-fs oracle validates only **layer totals, never the per-op mapping**. Two methods agreeing ≠ correct. Async collapse (6–8%→LBA 97–98%) shown only on the **own `async_reader.py` microbench**; the real engine (NIXL) never triggers collapse. LBA's 47.8% overlap-blind-spot coincides with the async regime → both can fail together in the realistic inference case (never run). Single-drive (extent map assumes one device). |
| **NIXL (real engine)** | ⚠️ **NEEDS-TEMPERING** | Genuinely real (`nixl_agent` GDS backend, real transfers, batch-op LBA parsing) — numbers solid. But framed as "the async engine where timing degrades" while the actual run (count=1, inflight 8) keeps **corr_id at 99.9%** → LBA is *redundant corroboration here, not a regime-rescue*. NIXL did not exercise the collapse. |
| **C3 — cuCIM WSI** | ⚠️ **NEEDS-TEMPERING** | (already known) 82% bypass real only on **naive per-tile** path (cuCIM's *benchmark* + naive users); production `read_region` coalesces and is clean. 19.5× lever is **sequential-only**. `results/cucim.md` is already honest; **`PDSW26-PAPER-PLAN.md` Fig 1/Fig 4 hook is not**. |
| **C3 — DeepNVMe** | ⚠️ **NEEDS-TEMPERING** | (a) "block_size gates throughput → explains autotuner" — throughput is **flat 2.85–2.87 GB/s across 1/4/16 MiB**; only the 256 KiB outlier moves, and the autotuner operates in the flat region. MDTS floor = one-drive artifact. (b) GDS-vs-AIO: the *unique* per-op value needs a **mixed** run that was **never executed**; the run that exists is also visible to nvidia-fs aggregate (cuCIM shape). (c) ZeRO-Inference 98.6%: **bounded slice (350/~9800 reads), OPT-1.3B toy model, SYNC regime** — not the async LBA-win the paper headlines. **No raw artifacts committed.** |
| **C3 — byte amplification** | ⚠️ **NEEDS-TEMPERING** | 2.000× is **real, exact, validated 4 ways** (better-measured than cuCIM). But it's a **constructed** microbench (`gdsio -U`, `embedding_gather.py` designed to produce it); the real libs (cuDF, elbencho) were **clean**. Commit 5c03e16 retracted "buy-faster-storage trap" (iostat *does* see aggregate 2×) — yet that retracted framing **still sits in `curated-pathologies.md` Case 1 → Table 3**. Only defensible novelty: per-tensor localization in a **mixed** workload. |
| **C3 — curated anti-pattern suite (Table 3)** | 🔴 **SHAKY as framed** | **100% synthetic** ("We constructed them… NOT demand," `curated-pathologies.md:5-6`). Case 1 wrong-decision claim self-recanted; Case 2's 5.1× is half **trivial coalescing** (fewer/larger ops), not attribution-driven; only Case 3 is genuinely attribution-unique. "You built the workload to make your tool win" = the #1 PDSW reviewer red flag, and the files concede it. Numbers drift across commits (2.8×→2.3×). |

## Two systemic issues (bigger than any single claim)

1. **The empirical "real-world value" evidence is thin.** *Every battle-tested library tested was
   clean* (cuDF, elbencho, NIXL, DeepNVMe production path, cuCIM `read_region`). All pathologies live
   in **naive or synthetic** code. The repo's own recurring line — *"well-engineered code is clean;
   naive code under-delivers"* — is the honest finding. The plan's "GDS silently fails in the wild,
   here's the tool that catches it" framing is **not** what the data supports.

2. **C1 (the backbone method) has no independent per-op oracle.** Its headline accuracy is
   self-/mutual-consistency. This is fixable but currently mislabeled as "validated vs oracle."

## What this means + recommended experiments

- **C2 (tool-blindness) is the solid core.** Keep it; keep the "per-op" qualifier.
- **C1 is salvageable and worth shoring up** — highest-value experiment: **build a real per-op oracle**
  (synthetic workload, each op a unique non-overlapping range, true op→range map recorded out-of-band;
  score corr_id and LBA against it). Plus run the **async-collapse + LBA-overlap case together** to show
  whether both methods fail in the realistic inference regime. This converts "consistency" → "accuracy."
- **C3 needs either (a) a real pathology that survives production-path scrutiny, or (b) an honest
  pivot**: reframe the thesis as *"method + measured characterization that production GDS stacks are
  clean and the silent gaps are naive-usage-specific, invisible to aggregate tools"* — which the data
  actually supports and is still novel. Forcing a "production is broken" headline repeats the cuCIM error.
- **Paper-plan edits required regardless:** temper Fig 1 (cuCIM naive-vs-production), Fig 5 (DeepNVMe
  flat-throughput region), Table 3 (relabel curated suite "synthetic capability demo," drop Case 1
  wrong-decision, foreground Case 3), and the C1 "accuracy vs oracle" language.

**Bottom line:** not a fabrication problem — a *framing+validation* problem. The honest paper is
**method (C1, once oracle-validated) + tool-blindness (C2) + an honest characterization (C3-as-negative)**,
which is a credible PDSW workshop paper. The current plan's headlines overclaim and would draw the
exact "you built it to win / production is actually fine" critique cuCIM already exposed.
