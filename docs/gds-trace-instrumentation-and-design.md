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

## 4. Right host: DataCrumbs (eBPF), not DFTracer+GOTCHA
Re-evaluated after inspecting **[LLNL/DataCrumbs](https://github.com/LLNL/datacrumbs)** (`external/datacrumbs`)
— the **eBPF successor to DFTracer from the same authors** (Devarajan/LLNL). It **already unifies the
layers we would otherwise glue together**, which makes "DFTracer + a separate eBPF correlator"
redundant:
- **One eBPF framework, config-driven probe categories** (JSON): `type 0 = syscalls`,
  **`type 1 = kernel kprobes (bio, ext4, iomap, fscache)`**, **`type 2 = userspace uprobes (libc, MPI,
  IOR…)`**, + custom. libbpf + **bpftime** (has a CUDA-attach option).
- **Native cross-layer correlation built in**: every event carries `bpf_ktime_get_ns()` + pid/tid (+ a
  `pid_map` for durations) — *one clock across userspace and kernel*. Output feeds **DFAnalyzer**.

It also **fixes our interposition problem cleanly**:
1. **cuFile = a uprobe category (config, not code).** An eBPF **uprobe attaches to the symbol *address*
   in `libcufile.so`**, so it fires for `cuFileRead`/`cuFileReadAsync`/`cuFileBatchIOSubmit`
   **regardless of `dlsym`/PLT/GOT** — exactly what defeated LD_PRELOAD and complicated GOTCHA. uprobes
   can also read the call **arguments** (size/offset) from registers. Async/batch = more symbols listed.
2. **The NVMe/amplification side is already traced** (`type 1`: `bio`, `ext4`, `iomap`). The
   **cuFile-op → bio-request amplification** (our 2× at 1 MiB) is just correlating a `type 2` cuFile
   event to the `type 1` `bio` events on the same pid/tid/time window — both already in the stream.
3. **Catches non-cuFile reader I/O for free** — kvikio's sub-16 KiB POSIX appears as syscall/bio events.
4. **Survives the CUDA 12.8+ P2PDMA shift** — it traces the **block layer**, not `nvidia-fs.ko`.

**So we don't combine DFTracer + eBPF — DataCrumbs already *is* the combined, correlated, multi-layer
eBPF tracer.** Net-new work shrinks to: **(a)** a **cuFile probe category** (+ arg capture: size/offset),
**(b)** the **cuFile↔bio correlation/attribution analysis** in DFAnalyzer (amplification, GDS-vs-POSIX
path inferred from "cuFile op present but no DMA in window", where-time-went), **(c)** GPU/app context
(bpftime CUDA attach or user-stacks) to tie ops to the framework op/tensor.

**When DFTracer/GOTCHA would still be preferable (the only reasons):**
- **Unprivileged environments** — eBPF needs root/CAP_BPF + a recent kernel; GOTCHA/LD_PRELOAD don't.
  (We have root on Chameleon; production HPC varies; bpftime's userspace eBPF softens this.)
- **App/Python-semantic attribution** — DFTracer's in-process hooks map to the framework op/tensor more
  directly than eBPF (which sees C symbols + stacks).

**Verdict:** Build on **DataCrumbs** as the single host — add a **cuFile uprobe category** + the
**cuFile↔bio cross-layer correlation** in DFAnalyzer. That delivers the per-op GDS attribution with the
amplification/path/where-time-went that no NVIDIA tool provides, in one already-correlated framework,
and is future-proof against the P2PDMA stack shift. Keep DFTracer/GOTCHA only as an unprivileged-mode
fallback.

## 5. Caveat that shapes the design
CUDA **12.8+** lets NVMe use upstream **PCI P2PDMA without `nvidia-fs.ko`** (and WekaFS already bypasses
the driver) → the kernel oracle/counters we rely on **can disappear**. The cross-layer module should
target the **block/NVMe/P2PDMA** layer (eBPF) rather than assume `nvidia-fs.ko`, so it stays valid as
the stack evolves.
