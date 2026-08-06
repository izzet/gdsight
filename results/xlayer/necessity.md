# Necessity test: when is per-op cross-layer attribution a *must*? (2026-06-25)

> **SUPERSEDED IN PART, 2026-08-06.** The argument stands, the magnitudes moved. `corr_id`
> mis-billing on the KvikIO small class is **85-96%** across runs rather than a single 94%, and the
> NIXL class-B amplification is **4.00x** at 2048 B rather than 3.2x at 2560 B. Sources:
> `posix_addr_attr.txt`, `ucb_kvikio_fix.txt`, `nixl_crosscheck_reps.csv`.

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

---

# Result 3 — NIXL-sized variant (real transfer-engine KV sizes, no library confound) ✅

Addresses the reviewer's "representative-not-captured" critique by driving the GDS path with NIXL's
*actual* KV transfer size for a production model: **2560 B = Llama-3.1-70B block-access KV I/O at PP=8**
(from NIXL `benchmark/kvbench/test/inference_workload_matgen.py` + the llama3 model config; range
across configs 256 B–10 KB). Heterogeneous async workload (`workloads/gds_mixed.c`, `cuFileReadAsync`):
200×1 MiB page reads (class A) + 2000×2560 B KV reads (class B). Crucially NIXL uses its **own** GDS
backend (`cuda_gds`), issuing `cuFileRead` directly — **no kvikio 16 KiB threshold**, so this removes
the library-policy confound entirely. Scorer `tools/keystone_gds_score.py`.

| observer | class A | class B | verdict |
|---|--:|--:|---|
| **aggregate** device/app | — | — | **1.052× → benign** |
| **per-class via LBA** | 1.000× | **3.20×** | class-B KV reads amplify 3.2× at the device |
| **corr_id (async)** | — | 64% correct, **36% unattributed** | unreliable — can't give a trustworthy per-class device-byte number |
| **`gds_stats`** | sees 2200 cuFile ops | — | blind to the **amplification** (API-side; sees the 2560 B *request*, not its 8192 B *device* cost) |

**Two real engines, two mechanisms, one necessity.** The kvikio pipeline (Result 2) loses GDS by
*silent POSIX bypass* (gds_stats blind to the ops, corr_id mis-bills 94% to the clean class). The
NIXL/transfer-engine pipeline (here) keeps everything on GDS but pays *device read-amplification*
(3.2×) that gds_stats cannot see and async corr_id cannot reliably attribute (36% unattributed). In
**both**, only per-op address-based (LBA) attribution recovers the per-class truth. Demonstrating the
phenomenon across two real engines' actual workloads — by two different mechanisms — is the answer to
"is this one cherry-picked workload?": it is not.

Honest scope: NIXL is not *run* here (not installed on v3; full kvbench is distributed/etcd/torch) —
we replay its real KV size through the real GDS path; building NIXL to drive `nixl_agent`+`cuda_gds`
end-to-end is the AE/camera-ready stretch. corr_id is 64% here (partial collapse) vs 1.6% in the clean
oracle (Table 1) because small KV reads complete partly in-context; the 36% unattributed is the
decoupled tail — either way corr_id is untrustworthy for the per-class number, LBA is exact.

---

# Result 4 — REAL end-to-end NIXL engine, traced (the captured real-world keystone) ✅

The strongest answer to "representative-not-captured": we drive the **actual `nixl_agent` + `cuda_gds`
backend** (NIXL 1.3.0 from PyPI, `nixl-cu12`) doing true P2P file→GPU(VRAM) reads with NIXL's own
heterogeneous KV access pattern (200×1 MiB page reads + 2000×2560 B Llama-3.1-70B PP=8 KV reads),
traced unmodified under DATACRUMBS trace-all. Reproducible: `chameleon/setup_nixl_venv.sh`,
`workloads/nixl_gds_{probe,kv}.py`, `tools/run_nixl_e2e.sh`.

| observer | class A | class B | verdict |
|---|--:|--:|---|
| nvfs_io / p2p_dma_mapping | — | — | **2171 / 2171 → true P2P GDS confirmed on the real engine** |
| aggregate device/app | — | — | **1.052× → benign** |
| per-class via LBA | 1.000× | **3.200×** | NIXL's KV reads amplify 3.2× at the device |
| cuFile API (gds_stats-class) | — | — | NIXL issues **`cuFileBatchIOSubmit`** — 64 reads collapsed into **one** API call → the API layer is batch-granular and cannot separate per-read |

**Two things this nails.** (1) The real-engine numbers **match the synthetic NIXL-sized run (Result 3)
exactly** (aggregate 1.052×, class-B 3.20×) — proving that replay was faithful, and that the keystone
is not an artifact of our probe. (2) NIXL batches 64 reads into a single `cuFileBatchIOSubmit`, so even
a perfect cuFile-API monitor sees *one* op where the device did 64 — **per-NVMe-command LBA attribution
is the only thing that recovers the per-class device cost.** This is the captured real-world eval: a
production transfer engine's actual GDS path, attributed per-op cross-layer.

**cuFile-API layer + corr_id now captured on the real engine (gap CLOSED, 2026-06-25).** The
`cuFileBatchIOSubmit` uprobe initially showed 0 events: it *was* attached at the right offset
(`libcufile.so+0x14fac0`) but `run_cnt=0` while nvfs/nvme fired, because the `nixl` wheel binds its
bundled **cu13** `libcufile.so.0` (different inode) instead of the system one our uprobe sits on. Fix:
`LD_PRELOAD` the system `libcufile.so.0` → NIXL uses the probed `.so` → `cuFileBatchIOSubmit` fires (35
batch ops for 2208 device reads). We now have the **full real-engine cross-check** — cuFile API
(batch-granular) + corr_id (87% unattributed under async batch) + LBA per-class — plus a KV-size sweep
(16×/6.4×/3.2×/1.6× across real model-parallel configs, exact closed-form match) and 3-run robustness,
**cross-checked against nvidia-fs `/proc` stats and `iostat` (both benign 1.05× aggregate, can't split
per class)**. Full irreplaceability table: **`results/xlayer/nixl-e2e-complete.md`**.

**Remaining honest scope:** single-agent NIXL transfer with a NIXL-derived access pattern (real engine,
real backend, real KV sizes) — not a captured multi-node prefill→decode `kvbench` trace
(distributed/etcd/torch; future work). `cupy` used only as a VRAM allocator (its compute kernels
JIT-fail on this driver — irrelevant to the GDS path).
