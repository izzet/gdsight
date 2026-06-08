# Related work: cross-layer I/O tracing, eBPF storage analysis, and GDS — and the gap we fill

Survey of the prior work nearest to GDS-Trace (read 2026-06-08), to position the contribution and find an
angle. Two questions per paper: **(a) what layers does it span / how**, and **(b) does it propose a
mitigation, or stop at tracing + characterization?**

## A. Userspace cross-layer I/O tracers (stop at POSIX; mostly stop at tooling)
- **Recorder** (uiuc-hpc; SC/IPDPS, arXiv:2501.04654). Multi-level LD_PRELOAD interposer tracing
  **HDF5 → MPI-IO → POSIX** with call-depth, so a high-level call's decomposition through middleware to
  syscalls is visible. **Stops at POSIX — no kernel/block/device.** Contribution = tracer + analysis;
  **no mitigation.** Eval: instrumentation overhead, trace size, case studies.
- **DFTracer** (LLNL/ANL/IIT/FSU; **SC'24**, DOI 10.1109/SC41406.2024.00023 — *our group; Yildirim is a
  co-author*). Multi-level data-flow tracer: **application APIs + system calls (POSIX/STDIO)**, an
  analysis-friendly compressed format, and workflow-context event tagging; 1–5% overhead, 1.3–7.1×
  smaller traces than Score-P/Recorder/Darshan, and it catches I/O from **dynamically spawned processes**
  the others miss. Contribution = tracer + format + analysis (DFAnalyzer); **no mitigation.** *We build
  directly on DFTracer/DFAnalyzer.*
- **Darshan + DXT / tf-Darshan** (darshan-hpc; tf-Darshan arXiv:2008.04395). Darshan = aggregate POSIX/
  MPI-IO counters; DXT = full userspace trace; tf-Darshan = fine-grained ML I/O. **Userspace only**;
  characterization, **no mitigation.**

## B. eBPF / kernel cross-layer storage analysis (reach the kernel; CPU path; stop at characterization)
- **zns-tools** (atlarge, 2024). **eBPF kprobes/tracepoints across VFS → block → zone**, correlating
  **file → LBA → zone** to show write-heat/placement on **ZNS SSDs**. The closest *method* to ours
  (eBPF, cross-layer, in-kernel correlation). But: **CPU storage path (no GPU/GDS)**; correlation is
  **file↔LBA↔zone, not per-application-op**; and it **explicitly stops at characterization** ("understanding
  cross-layer behavior is essential" → enables future optimization, proposes none).
- **IOscope** (eBPF). Single-purpose tracer of on-disk **file-offset access order**; characterization only.

## C. GPUDirect Storage work (enabling / characterization; not a cross-layer tracer)
- **GPU Direct I/O with HDF5** (Ravi, Byna, Koziol; **PDSW'20**, ~6 pg). **Builds** an HDF5 **GDS Virtual
  File Driver** so HDF5 apps use GDS without explicit GPU↔CPU copies; evaluates VFD vs explicit copy
  (GDS wins for ≥256 MB writes). This is the **mitigation/enabling route** (build a feature + evaluate),
  not a tracer. Motivates GDS by eliminating the host **"bounce buffer."**
- **ESPN** (arXiv:2312.05417, ISMM'24). A GDS multi-vector retrieval system; its authors **hand-aligned**
  CLS+BOW embeddings to cut 2 blocks→1/doc — the exact per-op waste our tool flags automatically.
- **gds_stats / cuFileGetStats, nvidia-fs `/proc`, cuFile NVTX→Nsight** (NVIDIA). Aggregate cuFile/device
  counters + a userspace-only cuFile-API timeline ("not supported for nvidia-fs.ko"). Per-op below cuFile:
  none.

## The gap (what each reaches)
| work | cuFile/app op | nidia-fs (kernel) | NVMe (device) | per-op cross-layer attribution | GPU/GDS path |
|---|:--:|:--:|:--:|:--:|:--:|
| Recorder / DFTracer / Darshan | ✓ (to POSIX) | ✗ | ✗ | ✗ (stops at POSIX) | ✗ |
| zns-tools / IOscope (eBPF) | ✗ | ✓ (VFS/block) | ✓ | by LBA/zone, not app-op | ✗ (CPU/ZNS) |
| Ravi HDF5-GDS-VFD | — (enabling) | — | — | — | ✓ (enables) |
| **GDS-Trace (this work)** | **✓ (cuFile)** | **✓ (nvidia-fs)** | **✓ (NVMe)** | **✓ per-op (corr_id)** | **✓** |

No prior tool does **per-op cross-layer attribution down the GPUDirect Storage path (cuFile↔nvidia-fs↔
NVMe)**: the userspace tracers stop at POSIX and never see GDS's P2P path; the eBPF cross-layer tools
reach the kernel but on the CPU/ZNS path and correlate by LBA, not by application op.

## The angle this reveals
1. **Positioning is clean and defensible:** we are the *downward extension* of the DFTracer/DFAnalyzer
   lineage (our own SC'24 tool) into the GDS kernel/device layers — combining Recorder/DFTracer's
   cross-layer call-attribution, zns-tools' eBPF in-kernel correlation, and applying it to Ravi's GDS
   domain, with **per-op application attribution (corr_id)** that none of them have.
2. **Most of this category stops at tracing/characterization** (Recorder, DFTracer, zns-tools, Darshan,
   IOscope) — so a *tool + characterization* paper is an **accepted PDSW/SC contribution shape**; our
   honest "robust-stack characterization + curated cases" fits it.
3. **But the strongest angle is to NOT stop there.** Ravi went further (built a VFD). We can differentiate
   from the stop-at-characterization norm by **closing the diagnosis→fix loop**: the tool flags the per-op
   pathology (alignment/threshold/layout), we apply the fix it points to, and report the **measured win**
   (we already have ~2× from alignment, and the curated cases each name a concrete fix). "Cross-layer
   tracers tell you *where it hurts*; we additionally show the *fix and the speedup*, guided per-op."
   That's the contribution that lifts this above "yet another tracer."
