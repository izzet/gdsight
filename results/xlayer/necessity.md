# When per-op cross-layer attribution is required: heterogeneous async workloads

> **Partly superseded.** The argument stands; the magnitudes were revised by later runs. `corr_id`
> mis-billing on the KvikIO small class is 85-96% across runs rather than a single 94%, and the
> NIXL class-B amplification is 4.00x at 2048 B rather than 3.2x at 2560 B. Sources:
> `posix_addr_attr.txt`, `ucb_kvikio_fix.txt`, `nixl_crosscheck_reps.csv`.

## Question

Is there a regime where per-op cross-layer attribution is the only way to obtain the correct result?
Heterogeneous async workloads are such a regime.

## Result 1: controlled mixed workload

### Setup

`workloads/gds_mixed.c` issues two op classes, scattered so reads do not merge: class A = 200×1 MiB
4K-aligned; class B = 2000×3 KiB at +3072 misalignment (spans 2 blocks). Scorer:
`tools/mixed_score.py`.

### Three observers

| observer | class A | class B | verdict |
|---|--:|--:|---|
| (1) aggregate device/app (iostat-vs-app) | — | — | 1.047×, looks benign (blind) |
| (2) corr_id (timing) per-class | 0.635× | 2.215× | wrong (A<1 is impossible; 36% of cmds unresolvable; under-counts B 17%, mis-bills to A) |
| (3) LBA (address) per-class | 1.000× | 2.667× | correct (100% of 2200 cmds classified) |

Class B is 3% of bytes but ~91% of ops, each wasting 63% of its device bandwidth, and the aggregate
ratio (1.05×) hides it completely.

### Interpretation

1. The aggregate cannot localize per class (the information is lost in the average), and the class
   cannot be isolated in a real mixed run to measure it separately.
2. The timing method (corr_id) gives wrong per-class numbers under async decoupling.
3. Address-based per-op cross-layer attribution (LBA) is therefore the only observer that yields the
   true per-class device amplification. Both the eBPF cross-layer capture and the LBA algorithm are
   required, not merely convenient.

### Scope

- No aggregate tool, and not corr_id, can produce the correct per-class number; only per-op LBA can.
  This requires a non-isolable, mixed, async workload.
- "Averaging hides outliers" is partly intuitive. The non-trivial parts are (a) the quantified GDS
  monitoring blind spot (gds_stats/iostat report "healthy" while 91% of ops amplify 2.7×) and
  (b) corr_id returning an impossible per-class number (A=0.635×), a concrete failure.
- This is a synthetic mix: it demonstrates the mechanism, not a real pathology. A real mixed async
  GDS workload (RAG/inference: large weight reads plus small KV/embedding gathers) is needed; this had
  tooling obstacles here (python+kvikio segfault under datacrumbs_wrap; frameworks not installed).
  Result 2 addresses this.

---

## Result 2: real kvikio RAG mix, default configuration

### Setup

The tracer was rebuilt with `DATACRUMBS_TRACE_ALL_PROCESSES=ON`, so the eBPF probes capture
unmodified processes with no injection (no `datacrumbs_track`/`wrap`, which segfaulted on the
kvikio/cupy python stack). This also confirms that kvikio uses the system libcufile that is
uprobed. `workloads/rag_mixed.py` drives the real kvikio library: class A = 200×1 MiB shard reads,
class B = 2000×3 KiB scattered embedding/KV reads (a recognized RAG/inference pattern), 8 threads,
default kvikio configuration. Scored with `tools/mixed_score.py` (other-process noise is filtered out
automatically by the ovh.dat extent map).

### Four observers

- `gds_stats`/cuFileGetStats sees 200 cuFileRead, all class A, and reports GDS as healthy. The 2000
  small reads are below kvikio's 16 KiB threshold and go silently to POSIX, so they never enter cuFile
  and are invisible to gds_stats.
- (1) Aggregate `iostat`-vs-app = 1.047×, which looks benign.
- (2) corr_id on class B: 0% correct; 94% mis-billed to class A (the POSIX reads carry a stale
  per-thread corr_id from an earlier class-A cuFileRead), 6% unattributed. corr_id is actively wrong
  here, hiding B's cost inside class A.
- (3) LBA on class B: 100% attributable. Class B amplifies 2.67× at the device (15.6 MiB device for
  5.9 MiB requested) vs class A's 1.00×.

### Interpretation

- The workload's own GDS health tool reports healthy while 91% of its operations (a) silently bypass
  GDS and (b) waste 63% of their device bandwidth, and the timing-based attributor mis-bills that
  waste to the clean class. Three commonly trusted views each mislead in a different way.
- gds_stats is blind (the reads are not cuFile ops), iostat is blind (aggregate), and corr_id is
  wrong (0% correct, 94% mis-billed). Address-based per-op cross-layer attribution (LBA) is the only
  observer that recovers the per-class result; both the eBPF cross-layer capture and the LBA
  algorithm are required.
- The workload uses real kvikio in its default configuration; the bypass is kvikio's actual
  behavior, not forced. The A/B mix ratio is a representative RAG pattern chosen for this test, not a
  captured production trace.

Tracer modes: pid-filter (default, low overhead, targeted via `datacrumbs_track`) vs trace-all
(captures unmodified processes; rebuild flag). Tools: `workloads/rag_mixed.py`,
`tools/mixed_score.py`; controlled C analog `workloads/gds_mixed.c`.

---

## Result 3: NIXL-sized variant (real transfer-engine KV sizes, no library confound)

### Setup

The GDS path is driven with NIXL's actual KV transfer size for a production model, rather than a
chosen representative size: 2560 B = Llama-3.1-70B block-access KV I/O at PP=8 (from NIXL
`benchmark/kvbench/test/inference_workload_matgen.py` and the llama3 model config; range across
configs 256 B–10 KB). Heterogeneous async workload (`workloads/gds_mixed.c`, `cuFileReadAsync`):
200×1 MiB page reads (class A) and 2000×2560 B KV reads (class B). NIXL uses its own GDS backend
(`cuda_gds`), issuing `cuFileRead` directly with no kvikio 16 KiB threshold, so this removes the
library-policy confound. Scorer: `tools/class_attr_score.py`.

### Result

| observer | class A | class B | verdict |
|---|--:|--:|---|
| aggregate device/app | — | — | 1.052×, benign |
| per-class via LBA | 1.000× | 3.20× | class-B KV reads amplify 3.2× at the device |
| corr_id (async) | — | 64% correct, 36% unattributed | unreliable; cannot give a trustworthy per-class device-byte number |
| `gds_stats` | sees 2200 cuFile ops | — | blind to the amplification (API-side; sees the 2560 B request, not its 8192 B device cost) |

### Interpretation

The two engines lose efficiency by different mechanisms. The kvikio pipeline (Result 2) loses GDS
through a silent POSIX bypass (gds_stats is blind to the ops; corr_id mis-bills 94% to the clean
class). The NIXL/transfer-engine pipeline keeps everything on GDS but pays device read amplification
(3.2×) that gds_stats cannot see and async corr_id cannot reliably attribute (36% unattributed). In
both, only per-op address-based (LBA) attribution recovers the per-class result. The phenomenon
therefore appears in two real engines' workloads through two different mechanisms, not in a single
selected workload.

### Scope

NIXL itself is not run in this result (not installed on v3; full kvbench is distributed/etcd/torch);
its real KV size is replayed through the real GDS path. Driving `nixl_agent`+`cuda_gds` end-to-end
is done in Result 4. corr_id is 64% here (partial collapse) vs 1.6% in the clean oracle (paper
Table 1) because small KV reads complete partly in-context; the 36% unattributed is the decoupled
tail. Either way corr_id is unreliable for the per-class number, while LBA is exact.

---

## Result 4: real end-to-end NIXL engine, traced

### Setup

The actual `nixl_agent` + `cuda_gds` backend (NIXL 1.3.0 from PyPI, `nixl-cu12`) performs true P2P
reads from file to GPU (VRAM) with NIXL's own heterogeneous KV access pattern (200×1 MiB page reads
and 2000×2560 B Llama-3.1-70B PP=8 KV reads), traced unmodified under DATACRUMBS trace-all.

### Result

| observer | class A | class B | verdict |
|---|--:|--:|---|
| nvfs_io / p2p_dma_mapping | — | — | 2171 / 2171: true P2P GDS confirmed on the real engine |
| aggregate device/app | — | — | 1.052×, benign |
| per-class via LBA | 1.000× | 3.200× | NIXL's KV reads amplify 3.2× at the device |
| cuFile API (gds_stats-class) | — | — | NIXL issues `cuFileBatchIOSubmit`: 64 reads collapsed into one API call, so the API layer is batch-granular and cannot separate per-read |

### Interpretation

(1) The real-engine numbers match the synthetic NIXL-sized run (Result 3) exactly (aggregate 1.052×,
class B 3.20×), which shows the replay was faithful and the result is not an artifact of the probe.
(2) NIXL batches 64 reads into a single `cuFileBatchIOSubmit`, so even a perfect cuFile-API monitor
sees one op where the device did 64. Per-NVMe-command LBA attribution is the only method that
recovers the per-class device cost. This is a production transfer engine's actual GDS path,
attributed per op across layers.

### cuFile API layer and corr_id on the real engine

The `cuFileBatchIOSubmit` uprobe initially showed 0 events. It was attached at the right offset
(`libcufile.so+0x14fac0`) but `run_cnt=0` while nvfs/nvme fired, because the `nixl` wheel binds its
bundled cu13 `libcufile.so.0` (different inode) instead of the system one the uprobe is attached to.
Fix: `LD_PRELOAD` the system `libcufile.so.0`, so NIXL uses the probed `.so` and
`cuFileBatchIOSubmit` fires (35 batch ops for 2208 device reads). This gives the full real-engine
cross-check: cuFile API (batch-granular), corr_id (87% unattributed under async batch) and LBA
per-class, plus a KV-size sweep (16×/6.4×/3.2×/1.6× across real model-parallel configs, exact
closed-form match) and 3-run robustness, cross-checked against nvidia-fs `/proc` stats and `iostat`
(both a benign 1.05× aggregate; neither can split per class). Full comparison table:
`results/xlayer/nixl-e2e-complete.md`.

### Scope

Single-agent NIXL transfer with a NIXL-derived access pattern (real engine, real backend, real KV
sizes), not a captured multi-node prefill-to-decode `kvbench` trace (distributed/etcd/torch; future
work). `cupy` is used only as a VRAM allocator (its compute kernels JIT-fail on this driver, which is
irrelevant to the GDS path).

### Reproduce

`chameleon/setup_nixl_venv.sh`, `workloads/nixl_gds_{probe,kv}.py`, `tools/run_nixl_e2e.sh`.
