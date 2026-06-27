# GDSight — Pre-proposal Smoke Test Results (NCSA **Delta**, A100)

**Run by:** Claude (interactive, driven by Izzet Yildirim) · **Date:** 2026-06-06
**Node:** `gpua054.delta.ncsa.illinois.edu` (NCSA **Delta**, *not* DeltaAI), Slurm job `18923989`
**Partition/alloc:** `gpuA100x4`, 1× A100-SXM4-40GB, 16 CPU, 64 GB, account `bekn-delta-gpu`
**Following:** Appendix A of `gds-trace-brief.md`
**Companion to:** `SMOKE-TEST-REPORT.md` (the earlier **DeltaAI / GH200** run) — this is a *different cluster*.

---

## TL;DR

> **Headline (Step 0, definitive): this Delta A100 node is ALSO compat-only — same wall as DeltaAI, but for a more actionable reason.**
> `gdscheck -p` reports **every storage backend `Unsupported`** with `properties.use_compat_mode : true`, so cuFile services all I/O via the POSIX bounce-buffer path — no NVMe→GPU DMA. The cause here is precise: the **`nvidia-fs` kernel module is present on disk** (`/lib/modules/.../extra/nvidia-fs.ko.xz`, **v2.25.7**) **but not loaded**, and a non-root user cannot load it. There is also a **version-compat warning**: gdscheck says the driver supports `nvidia-fs ≤ 2.17.4`, but the installed module is 2.25.7.
>
> **Decision-gate consequence:** NO-GO / **BLOCKED on this node** — there is no true-GDS ceiling to fall below (`-x0 ≡ -x1`, see Step 1), and the fallback is global + announced, not silent/per-op. Same verdict as DeltaAI: *blocked by node config, not a refutation of the idea.*
>
> **Key NEW results that differ from the DeltaAI run (matter for the proposal):**
> - **USDT novelty question — REOPENED and answered "YES on x86."** Unlike DeltaAI's aarch64 libcufile (1.14/1.16, **zero** stapsdt probes), this **x86 libcufile 1.13.1 ships 1240 USDT probes**, including the semantically meaningful ones: **`cufio_gds_read/write`**, **`cufio_px_read/write`** (POSIX fallback), **`cufio_px_io_retry`**, bounce-buffer events **`cufio-internal-{read,write}-bb`/`-bb-done`/`-map`**, `cufio_rdma_read/write`, and a kernel-ish `nvfs_bio-*` provider. ⇒ The brief's hedge "possibly USDT" is **confirmed-yes for the x86 build** — per-op true-vs-fallback could be observed via bcc/perf USDT, *not only* GOTCHA. The answer is **platform/version-dependent**, which is itself a finding.
> - **`nvidia-fs` is installed (just unloaded).** On DeltaAI the module did not exist at all; here it is one privileged `modprobe` away (modulo the 2.25.7 vs ≤2.17.4 version caveat). The fix is admin action, not a software install.
> - **Local NVMe present & writable:** 1.5 TB Samsung NVMe (`nvme0n1`) mounted XFS at `/local` and `/tmp` — the substrate true-GDS would use if nvidia-fs were loaded.
> - **`/etc/cufile.json` exists** here (symlink → `/etc/alternatives/cufile.json`); on DeltaAI it was absent.
> - **NVTX present** (`CUFILE_NVTX`, `InitializeInjectionNvtx2`, `NVTX_INJECTION64_PATH`); **35** exported `cuFile*` dynamic symbols → GOTCHA/LD_PRELOAD still fully viable.

---

## Environment

| Item | Value |
|---|---|
| Cluster | **NCSA Delta** (x86_64) — distinct from DeltaAI (GH200/aarch64) |
| Node | `gpua054` — ProLiant XL645d Gen10 Plus |
| GPU | 1× **NVIDIA A100-SXM4-40GB**, BAR1 64 GiB, "supports GDS" |
| NVIDIA driver | **570.148.08**, CUDA driver 12080 (12.8) |
| GDS tools | `/opt/nvidia/cuda-12.8/gds/tools/{gdscheck,gdsio,gds_stats}` — GDS release **1.13.1.3**, libcufile **2.12** |
| libcufile | `/opt/nvidia/cuda-12.8/lib64/libcufile.so.1.13.1` |
| Privileges | non-root (`uid=77280 izzet`) |
| IOMMU | **Disabled** (acceptable/recommended for GDS) |
| `nvidia_fs` kernel module | **Present on disk** (`.../extra/nvidia-fs.ko.xz`, **v2.25.7**) but **NOT loaded** (`/sys/module/nvidia_fs` and `/proc/driver/nvidia-fs` absent); driver warns supported only for `nvidia-fs ≤ 2.17.4` |
| `/etc/cufile.json` | present (→ `/etc/alternatives/cufile.json`) |

**Storage layout (`gpua054`):**

| Mount | Backend | Notes |
|---|---|---|
| `/local`, `/tmp` | **node-local NVMe** `/dev/nvme0n1p1`, **XFS**, 1.5 TB | Samsung `MZXL51T6HBJR`; `/tmp` writable → used as NVMe scratch |
| `/scratch`, `/work/nvme` | **Lustre** (dltawork) | parallel FS; GDS `Unsupported` without nvidia-fs |
| `/projects`, `/u`, `/sw` | NFS/other | repo lives in `/projects/bekn/izzet/gdstrace` |

---

## Step 0 — Environment & GDS config sanity  ✅ (gate result: COMPAT-ONLY, BLOCKED)

**`gdscheck -p` (key lines):**
```
GDS release version: 1.13.1.3 ; libcufile version: 2.12 ; Platform: x86_64
DRIVER CONFIGURATION:
  NVMe P2PDMA : Unsupported   NVMe : Unsupported   NVMeOF : Unsupported   SCSI : Unsupported
  DDN EXAScaler/IBM Spectrum Scale/NFS/BeeGFS/WekaFS : all Unsupported
  Userspace RDMA : Unsupported (rdma library Not Loaded, rdma devices Not configured)
CUFILE CONFIGURATION:
  properties.use_compat_mode  : true
  properties.force_compat_mode: false
GPU INFO: GPU 0 NVIDIA A100-SXM4-40GB supports GDS, IOMMU State: Disabled
PLATFORM INFO: IOMMU disabled;
  Nvidia Driver Info Status: Supported only on (nvidia-fs version <= 2.17.4)
  Cuda Driver Version Installed: 12080 ; Platform verification succeeded
```
**Reading:** GPU/driver/IOMMU side is healthy ("Platform verification succeeded"), but every *storage* backend is `Unsupported` and `use_compat_mode: true` because `nvidia-fs` is not loaded. Net: every `cuFileRead`/`cuFileWrite` is serviced via POSIX (read into a host bounce buffer, then `cudaMemcpy`), not GPUDirect DMA.

**`nvidia-fs` module state:**
- Loaded modules: `nvidia, nvidia_uvm, nvidia_drm, nvidia_modeset` (+ `gdrdrv` as a dep) — **no `nvidia_fs`**.
- On disk: `modinfo nvidia_fs` → `/lib/modules/5.14.0-427.91.1.el9_4.x86_64/extra/nvidia-fs.ko.xz`, `version: 2.25.7`, vermagic matches running kernel.
- `modprobe nvidia_fs` (non-root) → no-op (module still absent from `/sys/module`); loading requires root/CAP_SYS_MODULE.
- ⇒ **A privileged `modprobe nvidia_fs` would be required to even attempt true-GDS — and the 2.25.7 vs ≤2.17.4 driver-compat gap must be resolved by NCSA first.**

**USDT tracepoint check (reopens & refines a brief novelty question):**
- `readelf -n libcufile.so.1.13.1` → `.note.stapsdt` present, **1240 stapsdt notes**. (DeltaAI aarch64 1.14/1.16 had **0**.)
- Semantically named probes that matter for this project:
  - GDS path: **`cufio_gds_read`**, **`cufio_gds_write`**, `cufio_gds_unaligned_write`
  - POSIX fallback: **`cufio_px_read`**, **`cufio_px_write`**, `cufio_px_io_retry`
  - Bounce buffer: `cufio-internal-read-bb`, `cufio-internal-write-bb`, `cufio-internal-bb-done`, `cufio-internal-map`, `cufio-internal-io-done`, `cufile-internal-rmw`
  - RDMA: `cufio_rdma_read`, `cufio_rdma_write`
  - Providers incl.: `cufio`, `cufio-px`, `cufio-rdma`, `cufio_core`, `cufio_batch`, `cufio_async`, `cuf-p2p`, **`nvfs_bio`**, `cufio-stats`, `cufio-topo-nvfs`, …
  - (The bulk of the 1240 are auto-generated line-numbered probes like `cufio-px-538`; the named ones above are the manually-placed semantic events.)
- **NVTX** present: `CUFILE_NVTX`, `InitializeInjectionNvtx2`, `NVTX_INJECTION64_PATH`.
- **35** exported `cuFile*` dynamic symbols (`cuFileRead/Write`, `cuFileBufRegister`, `cuFileHandleRegister`, batch/async variants) → GOTCHA/LD_PRELOAD interposition fully viable.

**Architecture implication:** On **x86 Delta**, the per-op interception layer has *two* viable substrates — **(a) GOTCHA/LD_PRELOAD** over the 35 `cuFile*` symbols (portable, works everywhere incl. aarch64), and **(b) USDT** via bcc/perf on the named `cufio_gds_*` / `cufio_px_*` / bounce-buffer probes (x86-only here; absent on DeltaAI aarch64). The cross-platform-robust choice remains GOTCHA; USDT is a bonus signal where present.

---

## Step 1 — Establish the ceiling (gdsio, node-local NVMe `/tmp`)  ✅

8 GiB file, 1 MiB I/O, 4 threads, on local NVMe XFS (`/tmp/izzet_gds`). Same params across modes.

| `gdsio -x` mode | XferType | Throughput | Avg latency |
|---|---|---:|---:|
| `-x0` GPU_DIRECT (requested) | `GPUD` | **5.053 GiB/s** | 773 µs |
| `-x1` CPU_ONLY (storage→CPU) | `CPUONLY` | **5.304 GiB/s** | 736 µs |
| `-x2` storage→CPU→GPU | `CPU_GPU` | 4.988 GiB/s | 783 µs |
| `-x0` randread | `GPUD` | 5.088 GiB/s | 767 µs |
| (prep) `-x0 -I1` write | `GPUD` | 2.401 GiB/s | 1635 µs |

**Result (measured on `gpua054`):** the "GDS" path (`-x0`) is **statistically identical to / slightly slower than** the pure host path (`-x1`) — 5.05 vs 5.30 GiB/s. On a real GDS DMA path `-x0` should *beat* the bounce path; here it doesn't, because in compat mode `-x0` *is* the host POSIX path internally. **GDS-benefit delta on this node ≈ 0.** (This node's NVMe tops out ~5.0–5.3 GiB/s under this pattern — distinctly faster than DeltaAI's ~4.17, a genuine per-node difference, not an inherited assumption.)

## Step 2 — Controlled "silent pathology" demo  ✅ (not reproducible as intended — confirmed by measurement here)

**(a) The compat path is POSIX — proven on this node** via cuFile's own TRACE log (`CUFILE_LOGGING_LEVEL=TRACE`):
`NOTICE cufio-drv:830 running in compatible mode`; `Compatibility Mode: 1 Compat Read Mode: 1`; `WARN failed to open /proc/driver/nvidia-fs/devcount`. A 64-op read produced **192 `cufile_posix_read`** + 604 `cufio-px` (POSIX) trace events and **zero** `gds_read`. (Note: `strace` cannot be used to confirm syscalls here — gdsio exits 1 under ptrace because it breaks CUDA init — so the TRACE log + `/proc/.../fdinfo` were used instead.)

**(b) `gds_stats` cannot attach — tested against a LIVE process here:** `gds_stats -p <pid> -l 3` → `error opening gds stats … No such file or directory`, with **no cufile shm object in `/dev/shm`**. So the brief's primary Step-2 instrument (`cache_MB`/fallback counters) is unavailable in compat mode on Delta too.

**(c) Dual-fd routing confirmed live on this node** (the cause of the unaligned anomaly): the data file is opened twice —
- `fd40 flags=0140000` → **O_DIRECT=1** (aligned ops served here → device speed)
- `fd41 flags=02100000` → **O_DIRECT=0** buffered (unaligned ops served here → page cache)

**(d) Aligned vs unaligned randread (`-x0`, 8 GiB, 4 thr), measured on `gpua054`:**

| IO size | aligned | unaligned (`-U`) |
|---|---:|---:|
| 4 KiB | 0.177 GiB/s | 0.155 GiB/s |
| 64 KiB | 1.503 GiB/s | 2.847 GiB/s |
| 1 MiB | 5.113 GiB/s | **19.86 GiB/s** |

The unaligned 1 MiB reads hit **19.86 GiB/s** — *faster*, the opposite of a "GDS unaligned penalty" — because they route to the buffered fd and are served from page cache. This is a **page-cache / O_DIRECT artifact**, fully explained by the dual-fd routing above, with nothing GDS-specific. (Less extreme than DeltaAI's 39 GiB/s — this node has less RAM to cache the 8 GiB file with.)

## Steps 3 & 4 — Realistic workload + tool blindness  ✅ (moot on compat-only node — premise verified here, not inherited)

- **Vendor readers absent (checked here):** base `python3`/`python` = system interpreter, `numpy 1.20` only; **no kvikio, cupy, torch, or DALI**, and no conda envs at the standard path. The brief's named Step-3 readers aren't installed.
- **Moot regardless:** because 100% of cuFile I/O is POSIX here (measured in Step 2), a vendor reader could only re-demonstrate "all compat," never the *partial/silent/per-op* fallback the experiment is designed to catch. `gdsio` already served as the canonical NVIDIA cuFile generator above.
- **Tool-blindness is *inverted*, same as the gate logic:** the fallback here is global, deterministic, and **announced** by `gdscheck -p` (`use_compat_mode: true`) and `cufile.log` (`running in compatible mode`). There is no localized/silent loss for a coarse tool to miss — so this node cannot pose the blind-spot question.
- **USDT-attach is blocked for non-root here (tested):** although the probes *exist* (1240, incl. `cufio_px_read`/`cufio_gds_read`), `unprivileged_bpf_disabled=2`, no bpftrace/bcc installed, and `perf probe` fails with **"No permission to write tracefs … run with sudo."** So a live USDT demo needs root on this node; the probes' *static existence* is the solid, usable finding.

---

## Documentation & vendor guidance (researched before further trial-and-error)

NCSA's own docs and NVIDIA's GDS guides, checked to avoid guessing:

- **NCSA Delta docs do not appear to document GDS at all.** The Delta User Guide covers local NVMe scratch — **`/tmp` is node-local NVMe, 1.5 TB on GPU nodes (740 GB on CPU nodes), per-job, not shared** ([architecture](https://docs.ncsa.illinois.edu/systems/delta/en/latest/user_guide/architecture.html), [data management](https://docs.ncsa.illinois.edu/systems/delta/en/latest/user_guide/data_mgmt.html)) — but no GPUDirect Storage / cuFile / nvidia-fs page surfaced across multiple searches. ⇒ **GDS is not a documented or advertised user feature on Delta;** nvidia-fs being present-but-unloaded is consistent with "built into the image but not enabled/supported for users."
- **Two routes to true GDS exist; both are unavailable to a non-root user on this node as configured:**
  1. **Traditional `nvidia-fs.ko` path** — works on current 5.x kernels (an NVIDIA forum user got `NVMe: Supported` on **kernel 5.15** + OpenRM by loading nvidia-fs and setting `use_compat_mode:false`). Requires: `modprobe nvidia_fs` (**root**) + a GDS-supported FS. The module here is **2.25.7 — the version NVIDIA explicitly recommends** (release notes: 2.25.6 is buggy for GDS P2P, use ≥ 2.25.7). gdscheck's "Supported only on (nvidia-fs ≤ 2.17.4)" line looks like a **stale heuristic in this gdscheck build**, not the real constraint. → **This is the realistic path on Delta, and it is purely an admin action.**
  2. **New no-nvidia-fs PCI P2PDMA path (CUDA 12.8+)** — `properties.use_pci_p2pdma`. Requires **Linux kernel ≥ 6.2** + OpenRM ≥ 570.x + regkeys; not supported with RAID0/multipath. **This node runs kernel 5.14** → unavailable. This explains the TRACE line `Is PCIP2PDMA Supported: 0` even though the new `NVMe P2PDMA` row now appears in gdscheck. (Driver 570.148.08 ✓, but the kernel gates it.)

**Bottom line from the docs:** don't keep brute-forcing — neither GDS route can be enabled from userspace on this node (one needs a root `modprobe`, the other needs a newer kernel). Enabling it is an NCSA support / sysadmin action.

**Definitive doc check:** the entire NCSA Delta user-doc source (`github.com/ncsa/Delta_user_doc`, all 24 `.rst` pages) was grepped — **zero** mentions of `gpudirect`/`cufile`/`nvidia-fs`/`gdsio`/`p2pdma`/`use_compat`. GDS is genuinely undocumented/unexposed on Delta; the only storage guidance is "use node-local `/tmp` for many small files, copy results out before job end."

**Empirical "is there a userspace flag/trick?" test — NO.** The real cufile.json keys are `allow_compat_mode` (default `true`) and `use_pci_p2pdma` (default `false`). I wrote a custom config forcing **`allow_compat_mode:false` + `use_pci_p2pdma:true`** (`CUFILE_ENV_PATH_JSON=…/cufile_force.json`) to try to coax a true-GDS path. Result — it converts the *silent* fallback into a *hard failure*, confirming the blocker is the kernel module, not a config switch:
```
gdscheck -p : Platform verification error:
              nvidia-fs driver is not loaded. Set allow_compat_mode to true in cufile.json to enable compatible mode
gdsio -x0   : cuFile driver open error: 5001
cufile.log  : ERROR cufio-drv:825 nvidia-fs.ko driver not loaded
```
⇒ Flipping the flag cannot load a kernel module; `use_pci_p2pdma:true` is inert on this 5.14 kernel. **There is no userspace trick** — the fix is privileged (`modprobe nvidia_fs` by NCSA), full stop.

_Sources:_ [NVIDIA GDS docs](https://docs.nvidia.com/gpudirect-storage/) · [Troubleshooting & Install Guide](https://docs.nvidia.com/gpudirect-storage/troubleshooting-guide/index.html) · [Benchmarking & Config Guide](https://docs.nvidia.com/gpudirect-storage/configuration-guide/index.html) · [GDS Release Notes](https://docs.nvidia.com/gpudirect-storage/release-notes/index.html) · [Verifying P2P DMA for local NVMe (NVIDIA forum)](https://forums.developer.nvidia.com/t/how-to-verify-gpudirect-storages-p2p-dma-is-working-correctly-for-local-attached-nvme-ssd/304947) · [Delta User Guide](https://docs.ncsa.illinois.edu/systems/delta/en/latest/index.html)

---

## Required summary table (Appendix A)

| Workload (8 GiB, 4 thr, `/tmp` NVMe) | Ceiling `gdsio -x0` | Achieved | Penalty % | `cache_MB`/fallback seen? | Aggregate `gds_stats`/HTA/util reveal it? |
|---|---:|---:|---|---|---|
| 1 MiB aligned seq read | 5.05 GiB/s (`-x0`) ≡ 5.30 (`-x1`) | — | **0% (no GDS benefit to lose)** | **n/a — `gds_stats` won't attach in compat** | `gdscheck`/`cufile.log` already say "compat mode" |
| 1 MiB aligned **rand** read | 5.09 GiB/s | 5.11 | 0% | n/a | — |
| 1 MiB unaligned rand read | 5.11 GiB/s (O_DIRECT) | 19.86 (buffered/cache) | "−288%" = **page-cache artifact** | n/a | only `/proc/fdinfo` (O_DIRECT vs buffered) explains it |

---

## Decision gate  →  **NO-GO on this node (BLOCKED), same verdict as DeltaAI — reached independently by measurement on Delta**

- **GO requires:** a realistic workload below a **true-GDS ceiling**, the loss being a **silent fallback**, and **coarse tools unable to localize it**.
- **On `gpua054`:** there is **no true-GDS ceiling** (`-x0 ≡ -x1`, measured), the fallback is **not silent** (`gdscheck`/`cufile.log` announce global compat), and 100% of I/O is already POSIX (measured) — so the per-op blind spot can't even be posed. The one large bandwidth swing (unaligned 19.86 GiB/s) is a page-cache artifact.

⇒ **Blocked by node configuration, not a refutation of the idea.** The blind spot requires a node where GDS DMA works for *most* ops and a *subset* silently degrades — not this node.

## What this means for the brief

1. **The "runs on DeltaAI, no root" assumption is violated on *both* NCSA systems now — but for a fixable reason on Delta.** Delta GPU nodes ship `nvidia-fs` (the recommended 2.25.7) + local NVMe XFS; it's one privileged `modprobe` away. The userspace validation needs **NCSA to enable nvidia-fs on a GPU node** (or a node with kernel ≥ 6.2 for the nvidia-fs-free P2PDMA path). Confirm with NCSA support / muradli before proposing.
2. **The USDT novelty claim must be stated carefully — it is platform/version-dependent.** x86 libcufile **1.13.1 ships 1240 USDT probes** incl. `cufio_gds_read`/`cufio_px_read`/bounce-buffer events; DeltaAI's aarch64 1.14/1.16 ship **zero**. So "no USDT in libcufile" (the earlier report's claim) is **false on x86**. The robust, cross-platform interception substrate remains **GOTCHA/LD_PRELOAD** over the 35 exported `cuFile*` symbols (+ NVTX); USDT is a *bonus* signal where it exists.
3. **A supporting datapoint for the premise still stands.** Even in compat mode the per-op fallback fact lives only in unstructured TRACE spew, and `gds_stats` won't attach — the "no consumable per-op fallback signal" gap the project targets is real even here.
4. **Methodological lessons for the eventual GO run:** (a) page cache inverts bandwidth unless pinned (cold cache / file ≫ RAM / strict O_DIRECT); (b) `strace` is unusable with gdsio (breaks CUDA init) — use cuFile TRACE + `/proc/<pid>/fdinfo` flags instead; (c) `gdscheck -p`'s nvidia-fs-version line can be a stale heuristic — cross-check against the GDS release notes.

### Recommended next actions (maps to the brief's "Asks")
- **Ask NCSA support / muradli:** "Can `nvidia_fs` (2.25.7, already installed) be loaded on a Delta GPU node, with the local NVMe XFS registered for GDS — or is there a partition/node with kernel ≥ 6.2 for the CUDA-12.8 PCI-P2PDMA path?" That is the only place GO/NO-GO can be decided on NCSA hardware.
- **If neither:** the entire userspace validation (not just the kernel layer) moves to the LLNL/DataCrumbs testbed — which materially changes the brief's "runs on DeltaAI/Delta, no root" framing.
- **Independent of the above:** the USDT(x86)/NVTX/GOTCHA/symbol findings are final and go straight into the proposal's architecture section.

---

## Reproduction / artifacts
- This report: `SMOKE-TEST-REPORT-DELTA-A100.md`
- `results/delta-a100/gdscheck.txt` — full `gdscheck -p`
- `results/delta-a100/cufile_compat_evidence.txt` — compat notices + per-op POSIX counts
- `results/delta-a100/env_snapshot.txt` — node/kernel/driver/nvidia-fs/perf snapshot
- GDS tools: `/opt/nvidia/cuda-12.8/gds/tools/{gdscheck,gdsio,gds_stats}` · libcufile `/opt/nvidia/cuda-12.8/lib64/libcufile.so.1.13.1`
- Key commands: `gdscheck -p`; `gdsio -f <f> -d 0 -w 4 -s 8G -i 1M -I 0 -x {0,1,2}`; `readelf -n libcufile.so | grep stapsdt`; `gds_stats -p <pid> -l 3`
- *Scratch (`/tmp/izzet_gds/…`, incl. full `cufile_trace.log`) is node-local and is deleted when job 18923989 ends — key excerpts copied to `results/delta-a100/`.*

---
*Generated interactively during a Delta A100 allocation (job 18923989) on 2026-06-06. All numbers from live runs on `gpua054`; documentation cross-checked against NCSA + NVIDIA sources. Companion to the earlier DeltaAI report (`SMOKE-TEST-REPORT.md`).*
