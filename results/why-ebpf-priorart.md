# Prior-art check: "why eBPF" / tracer-overhead comparison for GDSight

Deep-research pass (2026-06-24, adversarially verified — 23/25 claims confirmed 2–3 votes) to decide
whether the proposed evaluation *"use DFTracer/Recorder/Score-P/TAU/Darshan-DXT to also capture cuFile
and show eBPF is less overhead/less cumbersome"* is novel, and whether the core eBPF-cross-layer-GDS
premise is unscooped. Bottom line up front:

- **Generic HPC-tracer overhead bake-off = SATURATED / already published** (incl. by our own group).
- **"eBPF captures cuFile with *less overhead*" = likely FALSE** at the API layer (kernel uprobe ≈10×
  costlier than LD_PRELOAD/GOTCHA) → a reviewer landmine; do **not** frame it that way.
- **eBPF *cross-layer GDS* tracer (cuFile↔nvidia-fs↔NVMe, per-op) = UNSCOOPED** → core novelty intact.
- **The honest, novel "why eBPF" is COVERAGE, not cost** + **report our own tool's overhead**.

## Q1 — Overhead bake-offs of HPC I/O tracers → **DONE (saturated)**
- **DFTracer (SC'24, Devarajan et al., DOI 10.1109/SC41406.2024.00023)** — overhead **1–5%** (3-0
  confirmed); benchmarks vs Score-P/Recorder/Darshan, 1.3–7.1× smaller traces. *Yildirim is a co-author.*
- **"Benchmarking Darshan and Recorder for HPC I/O Profiling and Tracing" (2025, IEEE Xplore 11164197)** —
  dedicated Darshan(+DXT) vs Recorder overhead/trace-size bake-off (3-0). DXT off-by-default *because* it
  adds tracing overhead; Darshan-DXT ≈ half Recorder's storage + less runtime overhead (3-0).
- **Recorder (arXiv:2501.04654)** and **tf-Darshan (arXiv:2008.04395)** each report their own
  overhead/trace comparisons. **Verdict:** re-running this on cuFile re-derives published methodology.

## Q2 — cuFile/GDS captured by a general-purpose tracer → **NOT DONE**
- Darshan modules = **POSIX, MPI-IO, STDIO, PnetCDF, HDF5**; DXT = MPI-IO+POSIX only (3-0). No cuFile.
- Recorder = **HDF5/PnetCDF/NetCDF/MPI-IO/POSIX** only; no cuFile/GDS/GPU (3-0).
- DFTracer = **POSIX/STDIO + app APIs** via GOTCHA/LD_PRELOAD; no cuFile/GDS/NVMe binding (2-1).
- The **only** existing cuFile observability is **NVIDIA's own** (NVTX→Nsight per-API-call; `gds_stats`
  aggregate; `cufile.log` TRACE) (2-1/3-0). `vfd-gds` *consumes* GDS, doesn't trace it (3-0).
- **Verdict:** nobody has captured cuFile with a general tracer — *but* that's because they stop at the
  POSIX/MPI-IO/HDF5 boundary and **cannot reach below the cuFile API**. That's a **coverage** gap, not an
  overhead story. (Small novel sliver exists, but it's a coverage point.)

## Q3 — eBPF for the GDS/cuFile path specifically → **NOT DONE (core novelty intact)**
- **zns-tools (ACM 10.1145/3642963.3652205)** — eBPF cross-layer, but **ZNS/CPU block path** (app→FS→block
  →NVMe/ZNS); **no GPU/GDS/cuFile/nvidia-fs** (3-0). Closest *method*, different *domain*.
- **gpu_ext (arXiv:2512.12615)** / **bpftime GPU** — eBPF on GPU **compute/memory/scheduling**, not
  storage; no GDS/cuFile/nvidia-fs (3-0).
- **IOscope** — eBPF, kernel **block** I/O patterns; not GPU (2-1).
- NVIDIA's GDS guide itself only recommends **generic Ftrace/Perf/BCC** with `funccount`/`funclatency` on
  **individual** nvidia-fs/p2p kernel funcs (e.g. `nvfs_mgroup_pin_shadow_pages`, `nvidia_p2p_get_pages`)
  — **per-function in isolation, NOT per-op cross-layer correlation** to the causing cuFile op (3-0).
- **Verdict:** no published or OSS eBPF tool does **per-op cuFile↔nvidia-fs↔NVMe attribution.** Unscooped.

## Q4 — eBPF uprobe vs LD_PRELOAD/GOTCHA overhead → kernel uprobe is the EXPENSIVE one
- **bpftime (arXiv:2311.07923):** kernel uprobe **3224 ns** vs userspace-eBPF **314 ns**; uretprobe
  3997 ns vs 381 ns → **≈10× kernel-uprobe penalty** (two context switches). Independently re-confirmed by
  our own earlier WebSearch ("10–20× slowdown").
- **LD_PRELOAD** ≈ **6.79 ns**/call (arXiv:2412.05784) — *userspace GOT/PLT interposition is far cheaper
  than any kernel-mediated hook.* **GOTCHA** (LLNL) = GOT-rewrite userspace interposition (what DFTracer uses).
- **Verdict:** at the **cuFile API layer**, DFTracer's GOTCHA hook is **cheaper** than our eBPF uprobe.
  Claiming "eBPF traces cuFile with less overhead" is **refuted by the literature** — do not say it.

## Q5 — "why eBPF" justified on COVERAGE → supported
- eunomia "GPU observability gap" (2025) frames the eBPF choice as **coverage/reach**, not overhead.
- NVIDIA's own guidance uses generic eBPF for the kernel path but only **per-function**, never cross-layer.
- **Verdict:** the defensible "why eBPF" is **kernel+device reach + one clock across layers + no relink +
  attach-to-running + survives the CUDA-12.8 P2PDMA stack shift** — things userspace interposition *cannot
  do at any overhead*. Overhead is a *tie-or-lose* axis; coverage is the *win* axis.

## What this means for the experiment (decision)
- ❌ **Do NOT** run a 5-tool "eBPF is less overhead on cuFile" bake-off: (1) the generic version is
  published (incl. ours); (2) the claim is likely false at the API layer; (3) only DFTracer can capture
  cuFile today — the other four would need new cuFile instrumentation *and still can't go below the API*,
  making it a strawman.
- ✅ **DO** reframe as **"instrumentation cost & coverage: why eBPF for GDS"** — see
  `results/instrumentation-cost-coverage.md` (the §5.x draft): (1) report **our own** per-op cross-layer
  overhead (expected, currently missing); (2) a **coverage matrix** naming the HPC tracers and the hard
  cuFile/nvidia-fs/NVMe boundary; (3) an honest **GOTCHA-vs-uprobe API-layer microbenchmark** framed as a
  *mechanism trade-off* (GOTCHA cheaper at the API; eBPF adds a small cost but uniquely extends below it).

**Reassurance:** nothing scoops the core premise. The eBPF storage cross-layer work is CPU/ZNS; the eBPF
GPU work is compute. The eBPF **cross-layer GPUDirect-Storage** tracer is ours.
