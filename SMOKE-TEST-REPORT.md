# GDSight — Pre-proposal Smoke Test Results (DeltaAI)

**Run by:** Claude (autonomous), on behalf of Izzet Yildirim · **Date:** 2026-06-06
**Node:** `gh151.hsn.cm.delta.internal.ncsa.edu` (NCSA DeltaAI), Slurm job `2426676`
**Following:** Appendix A of `gds-trace-brief.md`
**Status of this file:** written incrementally during the run (2 h alloc limit) — results appear as they are produced.

---

## TL;DR (updated as the run progresses)

> **Headline finding (Step 0, definitive): This DeltaAI node has NO working true-GDS path.**
> The `nvidia-fs` kernel module is not loaded, and `gdscheck -p` reports **every storage backend as `Unsupported`** with `properties.use_compat_mode : true`. cuFile therefore runs in **compatibility (POSIX + bounce-buffer) mode for all I/O** — there is no NVMe→GPU DMA to fall back *from*.
>
> **Consequence for the decision gate:** The smoke test as designed (show a workload below the *true-GDS* ceiling, lost to a *silent* per-op fallback that coarse tools miss) **cannot reach GO on this node**, because (a) there is no true-GDS ceiling to fall below — GDS mode == CPU bounce-buffer mode here — and (b) the fallback is *not* silent or per-op: `gdscheck -p` announces it globally in one line. This is a **NO-GO / BLOCKED-on-this-node** outcome, not a refutation of the idea.
>
> **What it changes in the brief:** the load-bearing assumption *"Userspace core — no root, runs on DeltaAI"* is **violated as-is**: DeltaAI (this node/config) does not expose a GDS DMA path to userspace, so validating the per-op fallback blind spot here first requires admin action (load `nvidia-fs` + configure a GDS-supported filesystem) or a different testbed.
>
> **Bonus results that stand regardless** (useful for the proposal):
> - **USDT novelty question settled: NO.** `libcufile.so` 1.14.0 (CUDA 12.9) and 1.16.1 (CUDA 13.1.1) contain **zero `.note.stapsdt` probes**. The per-op interception substrate cannot rely on bcc/perf USDT — it must use **GOTCHA/LD_PRELOAD** symbol interposition or **NVTX** injection.
> - **Interposition substrate confirmed viable:** libcufile exports **41 `cuFile*` dynamic symbols** (`cuFileRead`, `cuFileWrite`, `cuFileBufRegister`, `cuFileHandleRegister`, batch/stream/async variants) — all interposable by GOTCHA/LD_PRELOAD.
> - **NVTX present:** `CUFILE_NVTX`, `InitializeInjectionNvtx2`, `NVTX_INJECTION64_PATH` → Nsight-Systems-style cuFile tracing is real.
> - **`gds_stats` cannot attach in compat mode** (its stats `shm_open` region is never created without the GDS driver) — so the brief's *primary* Step-2 instrument is itself unavailable on a compat-only node.

---

## Environment

| Item | Value |
|---|---|
| Node | `gh151` — NCSA DeltaAI, 4× **NVIDIA GH200 120GB** (aarch64 / Grace-Hopper) |
| NVIDIA driver | 590.48.01 (Open Driver), CUDA driver 13.1 (13010) |
| CUDA toolkit (loaded) | `cudatoolkit/25.5_12.9` → `/opt/nvidia/hpc_sdk/.../cuda/12.9`; standalone installs 12.2–13.1.1 under `/sw/user/cudatoolkits/installs` |
| GDS tools used | `/sw/user/cudatoolkits/installs/cuda-12.9/gds/tools/{gdscheck,gdsio,gds_stats}` (GDS release **1.14.0.30**, libcufile **2.12**, gdsio 1.12, gds_stats v9) |
| Privileges | non-root (`uid=77280 izzet`), groups incl. `delta_bekn` — as expected for the userspace half |
| IOMMU | Pass-through / enabled; all 4 GPUs report `supports GDS` |
| `nvidia_fs` kernel module | **NOT loaded** (`/sys/module` has nvidia, nvidia_uvm, nvidia_drm, nvidia_modeset, nvidia_cspmu only); no `.ko` on disk; `/proc/driver/nvidia-fs` absent; `modinfo`/kmod tools not installed |
| `/etc/cufile.json` | absent → cuFile uses built-in defaults (compat allowed) |

**Storage layout (`gh151`):**

| Mount | Backend | Notes |
|---|---|---|
| `/local`, `/tmp` | **node-local NVMe** `/dev/nvme0n1`, **XFS**, 3.5 TB free | only place true GDS *could* work; `/local` not user-writable, **`/tmp` is writable** → used as NVMe scratch |
| `/projects`, `/work`, `/taiga` | **Lustre** (taiga / dltawork) | GDS reports Lustre `Unsupported` here (no nvidia-fs) |
| `/u`, `/sw` | NFS | — |

---

## Step 0 — Environment & GDS config sanity  ✅ (gate result: COMPAT-ONLY)

**`gdscheck -p` (key lines):**
```
GDS release version: 1.14.0.30 ; libcufile version: 2.12 ; Platform: aarch64
DRIVER CONFIGURATION:
  NVMe : Unsupported   NVMeOF : Unsupported   SCSI : Unsupported
  DDN EXAScaler/IBM Spectrum Scale/NFS/BeeGFS/WekaFS/Lustre(*) : all Unsupported
  Userspace RDMA : Unsupported  (rdma library Not Loaded, rdma devices Not configured)
CUFILE CONFIGURATION:
  properties.use_compat_mode  : true
  properties.force_compat_mode: false
GPU INFO: GPU 0-3 NVIDIA GH200 120GB  supports GDS, IOMMU State: Pass-through or Enabled
PLATFORM INFO: Nvidia Open Driver Installed; Cuda Driver 13010; Platform verification succeeded
```
**Reading:** The GPU/driver/IOMMU side is healthy and "Platform verification succeeded" — but that line only certifies the *GPU/host* side. Every *storage* backend is `Unsupported` and `use_compat_mode : true`, because the `nvidia-fs` kernel module that bridges NVMe/Lustre→GPU DMA is not present. **Net: cuFile will service every `cuFileRead`/`cuFileWrite` via the POSIX compatibility path (read into a bounce buffer, then cudaMemcpy), not via GPUDirect DMA.** This is exactly the "unsupported / compat-mode only" condition the brief's Step 0 says to detect first.

**USDT tracepoint check (settles a brief novelty question):**
- `readelf -S/-n` on `libcufile.so.1.14.0` (CUDA 12.9) and `libcufile.so.1.16.1` (CUDA 13.1.1): **no `.note.stapsdt` section, 0 stapsdt notes.** → **No USDT probes ship in libcufile** on this platform.
- NVTX *is* present in both (`CUFILE_NVTX`, `InitializeInjectionNvtx2`, `NVTX_INJECTION64_PATH`).
- **41** exported `cuFile*` dynamic symbols (incl. `cuFileRead`, `cuFileWrite`, `cuFileBufRegister`, `cuFileHandleRegister`, `cuFileReadAsync`, batch/stream APIs) → **GOTCHA/LD_PRELOAD interposition is fully viable.**

**Implication for the architecture:** the per-op userspace interception layer should be built on **GOTCHA/LD_PRELOAD over the cuFile API** (+ optional NVTX injection), **not** on USDT/bcc/perf static probes — those do not exist to attach to.

---

## Step 1 — Establish the ceiling (gdsio, node-local NVMe `/tmp`)  ✅

8 GiB file, 1 MiB I/O, 4 threads, 4K-aligned sequential read. Same params across transfer modes.

| `gdsio -x` mode | XferType | Throughput | Avg latency |
|---|---|---:|---:|
| `-x0` GPU_DIRECT (requested) | `GPUD` | **4.170 GiB/s** | 938 µs |
| `-x1` CPU_ONLY (storage→CPU) | `CPUONLY` | **4.142 GiB/s** | 947 µs |
| `-x2` storage→CPU→GPU (explicit bounce) | `CPU_GPU` | 3.120 GiB/s | 1252 µs |
| (prep) `-x0 -I1` write | `GPUD` | 2.004 GiB/s | 1950 µs |

**Result:** the "GDS" path (`-x0`) and the pure host path (`-x1`) are **statistically identical (4.17 vs 4.14 GiB/s)**. On a node with a real GDS DMA path, `-x0` should *beat* the bounce-buffer path; here it doesn't, because `-x0` **is** the host path internally (POSIX `pread` into a host bounce buffer, then copy to GPU). **The GDS benefit delta on this node ≈ 0.** This is the empirical confirmation of the Step-0 `gdscheck`/TRACE finding — not just a config readout, but measured behavior.

> *Caveat:* these are honest **device-speed** numbers — cuFile compat opens the file `O_RDONLY|O_DIRECT` and issues `pread64` on the O_DIRECT fd (verified by `strace`: 512 × `pread64(fd_O_DIRECT, …, 1 MiB)`), so the page cache is bypassed and ~4 GiB/s reflects the local NVMe under this access pattern. Could not `drop_caches` (needs root), but O_DIRECT makes that moot for the aligned reads.

---

## Step 2 — Controlled "silent pathology" demo  ⚠️ (not reproducible as intended on this node)

Random reads, 4 threads, 8 GiB file, `-x0`. Aligned baseline vs `-U` (unaligned 4K offsets).

| Pattern | 4 KiB | 64 KiB | 1 MiB |
|---|---:|---:|---:|
| aligned randread (`-I2 -x0`) | 0.176 GiB/s | 1.967 GiB/s | 3.121 GiB/s |
| **unaligned** randread (`-I2 -x0 -U`) | 0.829 GiB/s | **12.80 GiB/s** | **39.10 GiB/s** |

At face value the unaligned reads are ~12× *faster* — the opposite of the expected "unaligned penalty." **This is a measurement artifact, not a GDS pathology, and I traced the cause with `strace`:**

- cuFile compat keeps **two fds** open on the file: `fd29 = O_RDONLY|O_DIRECT` and `fd30 = O_RDONLY` (buffered).
- **Aligned** ops satisfy O_DIRECT alignment → served on **fd29 (O_DIRECT)** → bypass cache → hit NVMe → ~3 GiB/s ("real" speed).
- **Unaligned** ops cannot satisfy O_DIRECT alignment → cuFile routes them to **fd30 (buffered)** → served from **page cache** (the 8 GiB file is resident in the GH200's huge RAM) → 39 GiB/s (memory speed).
- Verified: aligned run = `512 × pread64(29,…)`, `0 × pread64(30,…)`; unaligned run = `0 × pread64(29,…)`, `512 × pread64(30,…)`.

**Why the brief's Step 2 can't fire here:** the intended demo is "unaligned I/O triggers the GDS internal staging/bounce path and a *penalty*, surfaced by `gds_stats cache_MB`." But on this node there is **no GDS path to stage for** — *everything* is already POSIX. "Unaligned" only flips O_DIRECT→buffered, an effect of the host page cache, not of GDS staging. And the instrument the brief relies on is unavailable:

- **`gds_stats -p <pid> -l 3` cannot attach** — it fails with `error opening gds stats … No such file or directory`. Root cause (traced): libcufile creates its stats region via `shm_open`, but in compat mode **no shm object is ever created** (nothing in `/dev/shm`, no matching fd in the process). The cuFile statistics interface is bound to the GDS driver path. So `cache_MB`/fallback counters are **not observable** on this node at all.

What *is* observable, and is the honest version of the Step-2 result, is the **per-op POSIX attribution buried in the TRACE log** — every `cuFileRead` expands to `cufio:420 cuFileRead invoked → cufio-px:466 cufile_posix_read fd:29 … gpu addr → cufio-px:538 cufile_posix_read done`. The per-op fallback information *exists internally* but only as ~unstructured DEBUG/TRACE spew (≈968 KB of log for 256 ops); there is **no structured per-op counter** exposed. *(This is actually a point in favor of the project's premise — see Implications.)*

---

## Steps 3 & 4 — Realistic workload + "are the coarse tools blind?"  ⚠️ (moot on this node)

**Vendor app availability:** base `python3` is the system interpreter (numpy 1.17 only). The `vllm_b` conda env has `cupy 13.4.1` + `torch 2.7`, but **no `kvikio`, no DALI** — the two readers the brief names for Step 3 are not installed. Installing one was *not* pursued because it cannot change the outcome: on a compat-only node, **100% of cuFile I/O is unconditionally POSIX**, so a vendor reader would only re-demonstrate "all compat," never the *partial / silent / per-op* fallback the experiment is designed to catch. (And `gdsio` already serves as the canonical NVIDIA vendor cuFile generator, which I drove above.)

**The tool-blindness question, inverted:** the project targets the case where *a subset* of ops silently falls back while **aggregate** tools (averaged `gds_stats`, HTA "host wait", `nvidia-smi` "looks fine") hide the localized loss. On `gh151` the situation is the opposite of a *blind spot*:

| Coarse tool | What it reports here | Blind? |
|---|---|---|
| `gdscheck -p` | `use_compat_mode: true`, every backend `Unsupported` — **states the problem in one line** | **No — loudly correct** |
| `cufile.log` | `NOTICE running in compatible mode` + `WARN failed to open /proc/driver/nvidia-fs/devcount` | **No** |
| aggregate `gds_stats` | **cannot even attach** (no GDS driver) | N/A |
| `gdsio -x0` vs `-x1` | identical bandwidth → benefit = 0 | reveals it |

There is **no localized/silent loss to miss** — the fallback is *global, deterministic, and announced*. That is precisely why this node is a **NO-GO for the motivating experiment**: not because per-op attribution is worthless, but because the prerequisite (a working GDS path that a *subset* of ops fails to use) does not exist here.

---

## Required summary table (Appendix A)

| Workload (8 GiB, 4 thr, `/tmp` NVMe) | Ceiling `gdsio -x0` | Achieved | Penalty % | `cache_MB`/fallback seen? | Aggregate `gds_stats`/HTA/util reveal it? |
|---|---:|---:|---:|---|---|
| 1 MiB aligned seq read | 4.17 GiB/s (`-x0`) ≡ 4.14 (`-x1`) | — | **0% (no GDS benefit to lose)** | **n/a — `gds_stats` won't attach in compat** | `gdscheck`/`cufile.log` already say "compat mode" |
| 1 MiB aligned **rand** read | 3.12 GiB/s | 3.12 | 0% | n/a | — |
| 1 MiB unaligned rand read | 3.12 GiB/s (O_DIRECT) | 39.1 (buffered/cache) | "−1150%" = **page-cache artifact**, not GDS | n/a | only `strace` (fd O_DIRECT→buffered) explains it |

**Interpretation:** there is no true-GDS ceiling on this node — `-x0` ≡ `-x1`. The only large bandwidth swing observed is a host **page-cache / O_DIRECT** effect, fully explained by standard `strace`, with nothing GDS-specific to attribute.

---

## Decision gate  →  **NO-GO on this node (BLOCKED), not a refutation of the idea**

Reconciling against the brief's gate:

- **GO requires:** a realistic workload running **below the true-GDS ceiling**, the loss being a **silent fallback/amplification**, and **today's tools unable to localize it**.
- **On `gh151` (and any DeltaAI node in this config):** there is **no true-GDS ceiling** (`-x0 ≡ -x1`); the fallback is **not silent** (`gdscheck`/`cufile.log` announce global compat mode); and the per-op gap can't even be *posed* because 100% of I/O is already POSIX. The one observed anomaly (unaligned 12–39 GiB/s) is a page-cache artifact, fully explained by an off-the-shelf tool.

⇒ **The motivating experiment cannot reach GO here.** This is a **blocked / inconclusive** verdict tied to node configuration, **not evidence the blind spot is absent in general.** The blind spot the project targets can only manifest on a system where GDS DMA *actually works for most ops* and a subset silently degrades — which this node is not.

---

## What this means for the brief (the important part)

1. **A load-bearing assumption is violated as written.** The brief states: *"Userspace core — no root, runs on DeltaAI … This is the novel, demand-critical core."* On the DeltaAI GH200 node tested, **`nvidia-fs` is absent and cuFile is compat-only**, so DeltaAI does **not** expose a GDS DMA path to userspace. The userspace validation the brief assumed could be self-served on DeltaAI **requires admin action first** (load `nvidia-fs` + a GDS-supported filesystem mount), or a different testbed (the LLNL/DataCrumbs path the brief reserved for the *kernel* layer). **This should be confirmed with NCSA support / muradli before proposing** — possibly GDS is enabled on a different DeltaAI partition/node type, or via a module, that I'm not on.
2. **The USDT novelty question is settled — favorably and concretely.** No USDT probes ship in libcufile (1.14 / 1.16, aarch64). The brief can drop the hedge ("possibly USDT") and state plainly: per-op interception must be **GOTCHA/LD_PRELOAD over the 41 exported `cuFile*` symbols** (confirmed present) and/or **NVTX injection** (confirmed present). This *tightens* the novelty/feasibility story rather than weakening it.
3. **A real, if oblique, supporting datapoint for the premise.** Even in compat mode, the per-op fallback fact (`cufile_posix_read` per `cuFileRead`) lives **only in unstructured TRACE spew**, and the aggregate counter tool (`gds_stats`) **can't attach at all**. The "the info isn't exposed per-op in any consumable way" gap the project targets is visibly real even at this layer.
4. **Methodological lesson for the eventual GO experiment:** page cache will dominate and *invert* bandwidth comparisons unless controlled (cold cache via root `drop_caches`, files ≫ RAM, or strict O_DIRECT). Any future "achieved vs ceiling" number must pin the cache state or it is meaningless — `strace`-level verification of O_DIRECT-vs-buffered routing should be standard.

### Recommended next actions (maps to the brief's "Asks")
- **Before proposing:** ask NCSA/muradli whether *any* DeltaAI node/partition has `nvidia-fs` loaded + a GDS-certified FS (local NVMe XFS with nvidia-fs, or WekaFS/GPFS-GDS). If yes, re-run Steps 1–4 there — that is the only place GO/NO-GO can be decided. If DeltaAI categorically lacks it, the *entire* validation (not just the kernel layer) moves to the LLNL/DataCrumbs testbed, which materially changes the brief's "runs on DeltaAI, no root" framing.
- **Independent of the above:** the USDT/NVTX/GOTCHA findings are final and can go straight into the proposal's architecture section.

---

## Reproduction / artifacts (all under `/projects/bekn/izzet/gdstrace/`)
- `SMOKE-TEST-REPORT.md` — this file
- `results/run_steps.sh` — Step 1+2 gdsio driver (`SIZE=8G IOSZ=1M THREADS=4 bash results/run_steps.sh`)
- `results/cufile-stats.json`, `results/cufile-trace.json` — cuFile configs (`export CUFILE_ENV_PATH_JSON=…`)
- `results/step12_output.txt` — raw gdsio output; `results/gds_stats_sample.txt` — gds_stats failure
- `results/logs/cufile_*.log` — TRACE logs showing `running in compatible mode` + per-op `cufile_posix_read`
- GDS tools: `/sw/user/cudatoolkits/installs/cuda-12.9/gds/tools/{gdscheck,gdsio,gds_stats}`
- Key commands: `gdscheck -p`; `readelf -SW libcufile.so | grep stapsdt`; `nm -D libcufile.so | grep ' T cuFile'`

---
## Bonus (stretch goal) — DFTracer cuFile GOTCHA tracer, built & validated

With spare allocation time I prototyped the brief's **userspace core**: a per-op,
cross-layer **cuFile** tracer using **GOTCHA** (the mechanism Brahma/DFTracer
already use), in the `izzet/dftracer` fork.

- **Built GOTCHA from source** (`gotcha-install/`), then a 7-symbol cuFile
  interposer `libdftracer_cufile.so` (`cuFileDriverOpen/HandleRegister/Deregister/
  BufRegister/BufDeregister/Read/Write`).
- **Ran `gdsio` under it on `gh151`** → **261 events** captured cleanly. Each
  `cuFileRead` carries `size, file_offset, buf_offset, ret,` **`fd`, `file`,
  `compat_mode`,** per-op `bw_GBps` — i.e. the **cuFile↔POSIX cross-layer link**
  (handle→fd→filename) and per-op fallback attribution the brief proposes.
  Trace: `256 reads, 256 MiB, 256/256 compat_mode=true, fd30→/tmp/izzet_gds/t0.dat,
  per-op latency p50=377 µs`. A write run captured 64 `cuFileWrite` ops.
- This is a **working existence proof of the demand-critical core** — and note it
  surfaces the exact per-op fact (`this read used POSIX`) that `gds_stats` cannot
  (aggregate, and won't even attach here).

Artifacts:
- Prototype + README + integration guide: `dftracer/contrib/cufile_tracer/`
  (`dftracer_cufile.cpp`, `CMakeLists.txt`, `README.md`)
- In-tree DFTLogger-backed integration: `dftracer/src/dftracer/core/brahma/cufile.{h,cpp}`
  (drop-in; wiring documented in the README — `DFTRACER_ENABLE_CUFILE`)
- Captured traces: `results/gdsio_trace.pfw`, `results/gdsio_write_trace.pfw`

> Scope: validated in compat mode (the only mode this node offers); the hook
> points are identical on a true-GDS node (`compat_mode` would read false). A
> per-op *true-vs-fallback* kernel signal still needs the nvidia-fs kprobe layer
> the brief reserves for LLNL/DataCrumbs — but the userspace cuFile view +
> file/handle correlation works **today**.

---
*Generated autonomously during a 2 h DeltaAI allocation; numbers are from live runs on `gh151` on 2026-06-06. Interpretations are flagged as such; raw outputs are in `results/`. Stretch-goal code is in the `dftracer/` fork clone.*
