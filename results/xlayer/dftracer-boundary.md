# Where DFTracer ends, what DataCrumbs provides, and what is ours (related-work positioning)

The honest question a reviewer asks: *you build on DFTracer/DataCrumbs — what is actually new?* This
answers it by (1) steelmanning DFTracer to its theoretical ceiling, (2) drawing the line vs the
DataCrumbs substrate, (3) listing the optimizations only our device-grounded attribution can drive.

## 1. DFTracer today, and "what it could do" if extended to cuFile
DFTracer intercepts **user-space** calls via brahma/GOTCHA LD_PRELOAD — POSIX/STDIO (`read`,`pread64`,
`write`,`openat`,`mmap`,…) plus app-level Python events — and emits `.pfw` for DFAnalyzer. Nothing in
its sources touches cuFile, nvidia-fs, NVMe, or the kernel.

**Steelman.** Suppose we *extended* it: GOTCHA-wrap `libcufile` too. Then a DFTracer-based solution
*could*:
- capture per-op **cuFile API calls** (offset, size, latency, thread) — i.e. `cuFileGetStats` content,
  but per-op and in the app-phase timeline;
- by wrapping **both** cuFile *and* POSIX, **detect the silent GDS→POSIX bypass (UC-B)** — it would see
  "200 `cuFileRead` + 2000 `pread`" and flag the fallback. **We concede this is reachable in user
  space**, and such a tool *could* drive the UC-B fix (coalesce small reads above the threshold).

**Hard ceiling (even fully extended).** A user-space interceptor stops at the **syscall boundary**. It
sees *requested* bytes, never *device* bytes, and has **no device events** to correlate to. Therefore it
**cannot**, at any amount of engineering:
- measure **device read-amplification** — UC-A's 3.2×, the **512-vs-4096 effective grid**, the WSI
  1.605× — because device bytes live at `nvme_setup_cmd`, below libc;
- confirm **true-P2P vs host-bounce** (an `nvidia-fs` marker);
- **root-cause** device-queue interference — for UC-C it would see the cuFile call was *slow* (the
  symptom) but never *why* (which large reads' NVMe commands it queued behind);
- attribute **asynchronous** device I/O at all: the cuFile call returns before the device command fires,
  and there is no device event in its trace to attribute to — so the LBA basis is simply unavailable;
- give the UC-D "don't bother" verdict (needs the device command structure).

## 2. The line vs the DataCrumbs substrate (so the contribution is properly ours)
DataCrumbs is a **general-purpose eBPF tracer** (prior work, not ours): it provides the machinery to
attach uprobes/kprobes, eBPF maps, a ring buffer, system-wide `trace-all`, and `.pfw` emission (so
DFAnalyzer is reused). Out of the box it has **no notion** of GDS, cuFile, nvidia-fs, NVMe, or
cross-layer correlation — it is a substrate, the way LLVM is to a compiler.

**What is ours** (the GDSight plugins + analysis, none of which exists in DataCrumbs or DFTracer):
1. the **probe set** — the cuFile uprobe (offset/size) and the `nvidia-fs`/`nvme_setup_cmd` kprobes
   (P2P markers; `req->__sector`/`__data_len` via CO-RE) — i.e. *what* to probe across the GDS path;
2. the **three-map `corr_id` correlation** (`gdstrace_corr`: same-thread exact + worker-thread fallback)
   that links one `cuFileRead` to its nvidia-fs and NVMe events online;
3. the **LBA/FIEMAP address basis** in the analyzer — the time-independent attribution that survives
   async — and its composition with `corr_id` (disjoint failure modes).

DataCrumbs answers "how do I attach an eBPF probe and emit an event"; GDSight answers "*what* to probe
on the GDS path and *how* to attribute each NVMe command to its causing op, by time **and** address."

## 3. Capability + fix comparison

| | DFTracer (today) | DFTracer + cuFile GOTCHA (steelman) | **GDSight (ours)** |
|---|:--:|:--:|:--:|
| POSIX `read`/`pread` per op | ✅ | ✅ | ✅ |
| cuFile call (offset/size) per op | ❌ | ✅ | ✅ |
| **detect silent GDS→POSIX bypass (UC-B)** | ❌ | ✅ | ✅ |
| **device** read-amplification (UC-A, 512→4096) | ❌ | ❌ | ✅ |
| true-P2P vs host-bounce | ❌ | ❌ | ✅ |
| device-queue tail **root-cause** (UC-C) | ❌ | symptom only | ✅ |
| **async** per-op device attribution (LBA) | ❌ | ❌ | ✅ |
| "don't-bother" command-amp null (UC-D) | ❌ | ❌ | ✅ |
| traces **unmodified** GPU apps (no LD_PRELOAD) | ❌ (segfaults on cupy/kvikio) | ❌ | ✅ (eBPF trace-all) |
| **fixes it can drive** | app-phase / # reads | + coalesce-above-threshold (UC-B) | + align/pad to measured grid (UC-A), segregate queues (UC-C), *and* don't-bother (UC-D), + quantified device cost of UC-B |

## 4. The additional optimizations our approach drives
Everything below the syscall line, which is where the *actionable, system-specific* levers are:
- **UC-A** — align/pad/coalesce to the **measured effective grid** (4096, not the spec's 512): device
  bytes 3.2×→1.0×, 1.8× goodput. (Requires device bytes.)
- **UC-C** — **segregate** classes to drain device-queue head-of-line blocking: p99 4080→216 µs.
  (Requires device-command timeline.)
- **UC-D** — *do not* raise `max_sectors_kb`: conserved-occupancy, throughput/tail-neutral. (Requires
  device command structure; saves wasted effort.)
- **UC-B** — even where an extended DFTracer could *detect* the bypass, only we **quantify its device
  cost** (2.67× amplification of the bypassed reads), which is what prioritizes the fix.

**One-line related-work claim:** bypass *detection* is reachable by a (hypothetical) user-space
cuFile+POSIX interceptor; **device-grounded amplification, P2P confirmation, interference root-cause,
async attribution, and the negative results are not** — they need per-NVMe-command probes linked to the
causing op, which is the GDSight contribution on the DataCrumbs eBPF substrate.
