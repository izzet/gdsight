# §5.x draft — Instrumentation cost & coverage: why eBPF for GDS

Writing-ready draft for the PDSW'26 §5 subsection that justifies the eBPF design choice. Reframed
(per `why-ebpf-priorart.md`) from the original "eBPF captures cuFile with less overhead" idea — which
is **a re-derivation of published HPC-tracer bake-offs AND likely false at the API layer** — to a
**coverage-first, honest-cost** argument that is novel and defensible.

**Status of the numbers:** attribution-accuracy and where-time-goes numbers already exist
(`cross-layer-attribution.md`, `gdstrace-tracer-results.md`). The **two new measurements** this
subsection needs are flagged `[MEASURE]` — both are cheap on the v3 node.

---

## §5.x Instrumentation cost and coverage

A natural question is *why eBPF* — HPC already has mature userspace I/O tracers (DFTracer, Recorder,
Darshan-DXT, Score-P, TAU). We answer on two axes the reader actually cares about: **what each
mechanism can see (coverage)** and **what it costs**. The honest summary: at the cuFile *API* layer
userspace interposition is the cheaper mechanism, but it is **structurally blind below the API**;
eBPF accepts a small per-call cost to gain the **kernel+device reach** that per-op cross-layer GDS
attribution *requires* — reach that no userspace tracer can achieve at any overhead.

### Coverage: the cuFile boundary (Table A)
General-purpose HPC tracers hook the layers they were built for — HDF5, MPI-IO, PnetCDF, POSIX,
STDIO — none of which a cuFile call passes through (`cuFileRead` is not a syscall, not MPI-IO, not
HDF5). Darshan's modules and DXT cover POSIX/MPI-IO; Recorder covers HDF5/PnetCDF/NetCDF/MPI-IO/POSIX;
DFTracer covers POSIX/STDIO + app APIs via GOTCHA. Each therefore needs *new* cuFile instrumentation
to see even the top of the GDS stack — and even with it, **all stop at the userspace API**: the
nvidia-fs DMA path and the NVMe commands a cuFile op generates are below their reach. NVIDIA's own
tools split the same way: NVTX→Nsight gives a per-API-call timeline but is "not supported for
nvidia-fs.ko"; `gds_stats` and `/proc/driver/nvidia-fs` give aggregate counters with no per-op link.

| Mechanism | cuFile API | nvidia-fs (kernel) | NVMe (device) | per-op cross-layer | needs new code for cuFile? |
|---|:--:|:--:|:--:|:--:|:--:|
| Darshan / Darshan-DXT | ✗ | ✗ | ✗ | ✗ | yes (no module) |
| Recorder | ✗ | ✗ | ✗ | ✗ | yes |
| Score-P / TAU | ✗ | ✗ | ✗ | ✗ | yes (wrapper) |
| DFTracer (GOTCHA) | ✓¹ | ✗ | ✗ | ✗ | ✓ hook exists, API only |
| NVTX → Nsight | ✓ | ✗ | ✗ | ✗ | n/a (built-in, API only) |
| gds_stats / nvidia-fs /proc | agg | agg | ✗ | ✗ | n/a (aggregate) |
| NVIDIA-guide eBPF (funccount/funclatency) | ✗ | per-fn² | ✗ | ✗ | n/a |
| **GDS-Trace (eBPF, this work)** | **✓** | **✓** | **✓** | **✓ (corr_id + LBA)** | — |

¹ a per-op cuFile hook is *feasible* in GOTCHA (we prototyped one) but sees only the API. ² NVIDIA's
guide demonstrates BCC `funccount`/`funclatency` on *individual* nvidia-fs/p2p functions in isolation
— per-function, never correlated to the causing application op.

**The point:** the gap is not "nobody traced cuFile cheaply enough" — it is that **every userspace
mechanism is architecturally confined to the API layer**, so the cross-layer attribution that
diagnoses GDS pathologies (P2P-vs-bounce, device-command/byte amplification, silent compat fallback)
is unreachable from userspace *by construction*. One eBPF object spans all three layers on a single
`bpf_ktime_get_ns()` clock — that is the capability, not a speed contest.

### Cost: the honest microbenchmark (Table B) `[MEASURE]`
We are explicit that eBPF is **not** the low-overhead choice at the API layer. A kernel uprobe traps
via `int3` and incurs two context switches; the literature measures ≈**3.2 µs per kernel uprobe vs
≈0.31 µs for userspace eBPF (~10×)**, and userspace GOT/PLT interposition (LD_PRELOAD ≈ **6.8 ns**,
GOTCHA similar) is cheaper still. So for the cuFile API event alone, **DFTracer's GOTCHA hook costs
less than our uprobe.** We report this directly rather than hide it:

| Mechanism (cuFile-API event only) | per-call cost | source |
|---|--:|---|
| LD_PRELOAD / GOTCHA (userspace GOT) | ~tens of ns | GOTCHA; LD_PRELOAD ≈6.8 ns (arXiv:2412.05784) |
| userspace eBPF (bpftime uprobe) | ~0.31 µs | bpftime (arXiv:2311.07923) |
| kernel eBPF uprobe (our cuFile probe) | ~3.2 µs | bpftime (arXiv:2311.07923) |
| **GDS-Trace cuFile uprobe — measured here** | `[MEASURE]` | this work |

`[MEASURE]` = a `gdsio`/kvikio API-call microbench: GOTCHA-hooked cuFile vs our uprobe-hooked cuFile,
ns/call. Expected outcome: GOTCHA wins at the API layer; we state it plainly.

### Cost in context: end-to-end is dominated by I/O, not the probe ✅ MEASURED
The API-layer µs is the wrong denominator. A GDS op's time is **99.1% in nvidia-fs+device, 0.9% in
the cuFile API** (measured, `gdstrace-tracer-results.md`), and real ops are ≥µs–ms of DMA. So a
few-µs probe is amortized to a small *end-to-end* overhead.

**Table C — tracer overhead vs I/O size × pattern.** Metric = gdsio's own reported throughput
(measured inside its I/O loop where probes fire synchronously; excludes the fixed ~0.5 s server
start/stop). Full tracer attached (cuFile uprobe + nvfs/nvme kprobes + syscalls), cold cache, single
PM983, 4 threads, 256 MiB transfer (random) / 2 GiB (sequential, to lengthen short runs). Mean ±
sample-std; the **achieved-IOPS** column (baseline) is the mechanism axis.

| size | pat | base GiB/s ± sd | traced GiB/s ± sd | overhead | base kIOPS | N |
|---|--:|--:|--:|--:|--:|--:|
| 4K   | seq  | 0.224 ± 0.018 | 0.190 ± 0.007 | **15.0%** | 58.8 | 7 |
| 4K   | rand | 0.141 ± 0.001 | 0.129 ± 0.001 | **8.0%**  | 36.8 | 5 |
| 16K  | seq  | 0.623 ± 0.037 | 0.576 ± 0.021 | 7.7%  | 40.8 | 7 |
| 16K  | rand | 0.497 ± 0.001 | 0.462 ± 0.006 | 7.1%  | 32.6 | 5 |
| 64K  | seq  | 0.984 ± 0.135 | 0.972 ± 0.159 | 1.2%† | 16.1 | 7 |
| 64K  | rand | 0.731 ± 0.004 | 0.712 ± 0.003 | 2.6%  | 12.0 | 5 |
| 256K | seq  | 2.600 ± 0.008 | 2.554 ± 0.010 | 1.8%  | 10.7 | 7 |
| 256K | rand | 2.478 ± 0.011 | 2.442 ± 0.013 | 1.4%  | 10.1 | 5 |
| 1M   | seq  | 2.909 ± 0.003 | 2.919 ± 0.018 | ~0%   | 3.0  | 7 |
| 1M   | rand | 2.923 ± 0.004 | 2.921 ± 0.004 | 0.0%  | 3.0  | 5 |
| 4M   | seq  | 2.945 ± 0.006 | 2.943 ± 0.004 | 0.1%  | 0.8  | 7 |
| 4M   | rand | 2.926 ± 0.003 | 2.926 ± 0.003 | 0.0%  | 0.7  | 5 |

**The result is a single law, not a table of cases: overhead ≈ (per-op probe cost) × (achieved
IOPS).** Sorting by the IOPS column, seq and rand collapse onto one line; the apparent
"seq 15% vs rand 8% at 4K" is just different IOPS (58.8 vs 36.8 k) at the *same* per-op cost. A
through-origin fit gives **≈0.22 %/kIOPS ⇒ ~2.2 µs per traced `cuFileRead`** — order-of-magnitude
consistent with the literature kernel-uprobe cost (bpftime ~3.2 µs; we add uretprobe + nvme/syscall
kprobes, amortized), so the overhead model is **self-validating**.

Consequence: for **realistic GDS use (large transfers, low IOPS) overhead is ≈0%** (1M/4M, both
patterns, tight); it grows only for tiny high-IOPS ops — ~8% (rand 4K) to ~15% (seq 4K), the regime
where GDS is already a poor fit. This is the cost of the cross-layer reach that no userspace tracer
can provide *at any overhead* (Table A).

† **Honest caveat on sequential noise:** longer (2 GiB) runs did **not** tighten 4K/16K/64K-seq
(CV 8–16%); the variance is **intrinsic to mid-size sequential reads on this single PM983 — the
*baseline* CV is as high as traced, so it is not a tracer effect.** Those cells carry wide error
bars but still sit on the IOPS curve; conclusions rest on the tight cells (all random, + large seq).
Harness `tools/overhead_bench.sh`; merge+IOPS `tools/overhead_table.py`; raw/final in
`results/step5-overhead/`.

### Takeaway
- **Coverage decides the mechanism:** per-op cuFile↔nvidia-fs↔NVMe attribution is *only* reachable
  in-kernel; userspace tracers cannot do it at any overhead. (Table A)
- **We are honest about cost:** eBPF loses the API-layer microbench to GOTCHA/LD_PRELOAD (~10× per
  call), but (Table B) that cost is amortized by I/O-dominated ops to a small end-to-end overhead
  (Table C), in exchange for the cross-layer reach the diagnosis requires.
- **Bonus, operational reasons** (state in one sentence, don't over-claim): no relink, attach to a
  running process, and **survives the CUDA-12.8+ PCI-P2PDMA stack shift** (block layer, not
  `nvidia-fs.ko`) — where the proc counters disappear.

### What to actually run (2 cheap experiments on the v3 node)
1. **API-layer microbench** → Table B last row: GOTCHA cuFile hook vs eBPF-uprobe cuFile hook, ns/call
   (use the existing GOTCHA prototype + the DataCrumbs uprobe). Honest: GOTCHA wins.
2. **End-to-end overhead** → Table C: baseline vs probes-attached wall-clock + GiB/s on the 4 real
   workloads (drop_caches, repeat ×N, report mean±sd). Expected: low single-digit %.

### What NOT to run (and why — for the rebuttal file)
A 5-tool "eBPF vs DFTracer/Recorder/Darshan-DXT/Score-P/TAU on cuFile overhead" bake-off:
(1) the generic bake-off is **already published**, incl. our group's DFTracer SC'24 (1–5%; 1.3–7.1×
smaller traces) and the 2025 IEEE Darshan-vs-Recorder study; (2) "eBPF is lower overhead on cuFile"
is **refuted** by the uprobe-vs-GOTCHA literature; (3) only DFTracer can capture cuFile today, and the
other four cannot reach below the API — so it would be a strawman, not a fair comparison. Prior-art
evidence: `why-ebpf-priorart.md`.
