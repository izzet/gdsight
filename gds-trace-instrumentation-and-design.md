# NVIDIA GDS instrumentation: what exists, its scope, and the op-level cross-layer delta

What NVIDIA ships for instrumenting/profiling GDS, where it stops, what op-level cross-layer tracing
would add, and whether extending **DFTracer** is the right vehicle. Sourced from NVIDIA's GDS
[Troubleshooting](https://docs.nvidia.com/gpudirect-storage/troubleshooting-guide/index.html),
[Configuration/Benchmarking](https://docs.nvidia.com/gpudirect-storage/configuration-guide/index.html),
[Overview](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html), and
[cuFile API](https://docs.nvidia.com/gpudirect-storage/api-reference-guide/index.html) guides.

## 1. What NVIDIA provides (the full surface)

| mechanism | layer | granularity | per-op? timestamps? | key limits |
|---|---|---|---|---|
| **gds_stats** CLI + **`cuFileGetStatsL1/L2/L3`** API (`cuFileSetStatsLevel`, `StatsStart/Stop/Reset`) | cuFile userspace | per-process, per-GPU **aggregate** + size histogram; L3 = per-GPU **P2P/NVFS/POSIX/unaligned/sparse** counts | **No / No** | no per-op records; **no userspace latency**; no per-process BAR; blind to I/O that never enters cuFile |
| **/proc/driver/nvidia-fs/stats** | nvidia-fs kernel | per-GPU **aggregate** (Reads/Writes n·MiB·BW·lat, Bar1-map, Ops in-flight, Registered_MB, Cache_MB, errors) | **No / No** | kernel-submission→completion latency only; **absent for WekaFS**; **gone when NVMe uses CUDA 12.8+ P2PDMA without nvidia-fs.ko** |
| **/proc/driver/nvidia-fs/peer_affinity** | nvidia-fs kernel | per-GPU↔NIC aggregate; cross-root-complex flag | No / No | topology hint only |
| **cufile.log** (ERROR…**TRACE**) | cuFile userspace | per-event text log; `"cufile IO mode: POSIX"` fallback line | partial / **TRACE only** (hot-path perf cost) | unstructured text, 32 MB cap; not machine per-op records; TRACE too costly for production |
| **NVTX static tracepoints in libcufile.so** (`profile:nvtx`) → **Nsight Systems** | cuFile userspace | per-cuFile-API-call timeline | **Yes (API calls) / Yes** | **stops at the cuFile API — "not yet supported for nvidia-fs.ko"**; no correlation down to driver/NVMe; no app/tensor attribution |
| **Ftrace / BPF on nvidia-fs kernel fns** (`nvfs_mgroup_pin_shadow_pages`, `nvidia_p2p_get_pages`), `funclatency` | nvidia-fs kernel | per-function latency/count | per-fn / yes | **not tied to the originating cuFile op / file / app**; manual |
| **gdscheck** | cross-layer (static) | whole-system capability/config | n/a | static only; no runtime behavior |
| **gdsio / gdsio_verify** | end-to-end (synthetic) | whole-run aggregate BW/lat | No | load generator, not a workload profiler |
| **nvidia-smi / dcgmi** (rxpci/txpci), **iostat**, strace/perf/SAR | GPU-PCIe / block / syscall | per-GPU / per-device / per-syscall aggregate | varies | not GDS-aware; `cuFileRead` is **not a syscall** (invisible to strace); device-level only |
| **gds_log_collection.py** | cross-layer | point-in-time snapshot bundle | No | diagnostics, not tracing |

## 2. The scope / where it stops
NVIDIA gives exactly **two shapes** of data:
1. **Aggregate counters** — at the cuFile layer (`gds_stats`/API; counts P2P vs NVFS vs POSIX, sizes,
   BW, avg latency) **and** at the nvidia-fs kernel layer (proc stats). Per-process / per-GPU, no per-op.
2. **A cuFile-API timeline** — NVTX→Nsight, **userspace only**.

Three hard boundaries follow, each confirmed in their own docs:
- **Nothing below the cuFile API is correlated per-op.** NVTX explicitly stops at libcufile; **nvidia-fs
  has no tracepoints**; kernel funcs are ftrace-able but **not linked** to the cuFile op, file, or app.
- **No single per-op record spans the stack**: app-context → cuFile op (size, *path*, latency) →
  nvidia-fs (DMA, *splits/amplification*) → NVMe request. NVIDIA's "cross-layer" guidance is a
  **manual temporal-coincidence workflow** (run gds_stats + nvidia-smi PCIe + iostat side-by-side).
- **Blind to I/O that never enters cuFile** — e.g. kvikio's sub-16 KiB POSIX path: `gds_stats`/`cuFileGetStats` show **nothing** (we measured `Read ok=0`); only the kernel/block layer sees it.

So even the *programmatic* API (`cuFileGetStatsL3`) is aggregate: it tells you *how many* ops were
P2P/NVFS/POSIX, never *which* op, *why*, or *what it cost end-to-end*.

## 3. What op-level cross-layer tracing would ADDITIONALLY provide
A per-op record + the correlation NVIDIA lacks:
1. **Per-op, timestamped records** (not aggregates): `{app ctx (tensor/file/phase), offset, size, path
   = GDS-DMA | cuFile-compat-POSIX | reader-POSIX, userspace latency, kernel latency}`.
2. **Cross-layer correlation per op** — tie the cuFile op to the **nvidia-fs/NVMe requests it caused**
   (the layer NVTX explicitly doesn't cover): **amplification** (our 2× NVMe ops at 1 MiB-misaligned),
   true **DMA-vs-bounce**, and **where time went** (userspace vs kernel vs device) *per op*.
3. **Reader-layer visibility** — catch ops that bypass cuFile entirely (kvikio threshold; our
   `gds_stats`-invisible case) by hooking the reader API and/or the block/eBPF layer.
4. **App attribution** — map each op to the app operation / tensor / layer / dataset-chunk (what
   PyTorch/HTA can't cross into and Darshan can't see for cuFile).
5. **Causal answer to "why is my GPU stalled / no speedup"** — stall ← op ← path/amplification ← layer.
6. **Survives the stack shift** — works when **nvidia-fs.ko is bypassed** (CUDA 12.8+ P2PDMA, WekaFS),
   where the proc counters vanish, by tracing at the cuFile API + block/NVMe layer instead.

This is precisely the gap every real user hand-codes around (ESPN `cudaEvent`+manual BW; nixlbench
`std::chrono`; Muradli's hand-timing) and that `gds_stats` (`posix=0`) cannot express.

## 4. Is extending DFTracer the right approach?
**[DFTracer](https://akougkas.io/assets/pdf/dftracer.pdf)** (Kougkas/Sun lab — *same group as Muradli*)
is a GOTCHA-based, multi-level (app + POSIX I/O) per-op tracer for dynamic AI workflows, emitting
timestamped Chrome-trace records with a DFAnalyzer pipeline. Fit assessment:

**Why it's the right host:**
- Already does **GOTCHA interposition + per-op timestamped records + app-level + POSIX-level** events —
  adding a **cuFile module** is the "missing cuFile layer" the HPC I/O ecosystem lacks. Architecturally
  it's a new instrumented interface, exactly DFTracer's extension model.
- **Same lab** → collaboration/adoption/co-authorship (Muradli, ESPN-adjacent); reuses DFAnalyzer;
  can emit Darshan/Recorder-compatible records so IOAgent/ION can consume GDS traces.
- Gives app↔POSIX correlation for free; cuFile sits naturally between them.

**What DFTracer does NOT have and we must build (the real work / novelty):**
1. **A cuFile GOTCHA module over the *full* API surface** — sync **and** `*Async` **and** `cuFileBatchIO*`
   (kvikio/ESPN use batch/async, not `cuFileRead`), and it must survive **`dlsym`'d** resolution
   (we proved **LD_PRELOAD is insufficient**; GOTCHA patches the GOT and *can* catch dlsym'd symbols if
   wrapped before first use — must verify). Capture path (GDS/compat) per op.
2. **The cross-layer kernel correlation — the novel part.** DFTracer is userspace (libc POSIX). To tie
   a cuFile op to its **nvidia-fs/NVMe requests** (amplification, DMA-vs-bounce) needs a **new kernel
   source**: aggregate proc-stat deltas are *not* per-op → it must be **eBPF on nvidia-fs / NVMe / block
   (and PCI P2PDMA for CUDA 12.8+)**, joined to the cuFile op by thread/time/offset. This correlation
   is the research contribution; it does not exist in DFTracer or anywhere else.
3. **Reader-layer hooks** (kvikio/DALI) to catch sub-cuFile POSIX, or infer it from the block layer.
4. **NVTX bridge** — emit/ingest NVTX so traces compose with Nsight rather than competing.

**Verdict:** Extend DFTracer as the **host/scaffold** (interposition, per-op records, app attribution,
analysis, lab/ecosystem fit) — but the **cuFile-API GOTCHA module (incl. async/batch/dlsym)** and the
**eBPF cross-layer correlation to nvidia-fs/NVMe/P2PDMA** are net-new and are where the actual
contribution lives. DFTracer is the right *vehicle*, not a drop-in solution.

## 5. Caveat that shapes the design
CUDA **12.8+** lets NVMe use upstream **PCI P2PDMA without `nvidia-fs.ko`** (and WekaFS already bypasses
the driver) → the kernel oracle/counters we rely on **can disappear**. The cross-layer module should
target the **block/NVMe/P2PDMA** layer (eBPF) rather than assume `nvidia-fs.ko`, so it stays valid as
the stack evolves.
