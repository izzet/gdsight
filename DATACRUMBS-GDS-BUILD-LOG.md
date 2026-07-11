# DataCrumbs + cuFile/GDS — build & bring-up log

Goal: build **DataCrumbs** (eBPF multi-layer tracer, `external/datacrumbs` = izzet fork @ `develop`) on
the Chameleon A100 node, add a **cuFile uprobe probe category** (`libcufile.so`:
`cuFileRead/cuFileReadAsync/cuFileBatchIOSubmit/…`) so it traces GDS ops alongside the kernel `bio`
layer, run it on a GDS workload, and analyze the `.pfw.gz` trace with **DFAnalyzer**
(`external/dfanalyzer` = izzet fork @ `feat/datacrumbs`). Per-op cross-layer attribution = the goal.

## Node state (2026-06-08)
- kernel `6.8.0-1051-nvidia`, BTF present ✓, kernel headers at `/lib/modules/$(uname -r)/build` ✓
- clang 18.1.3 ✓, cmake 3.28 ✓
- **libbpf 1.3.0 (apt)** — datacrumbs needs **≥1.5.0** → building 1.5.x from source
- **bpftool** only present for generic `6.8.0-124` (apt wrapper won't match `-nvidia`) → building v7.5.0 from source
- eBPF needs root — we have it.

## Plan / status
- [x] **deps**: built libbpf v1.5.0 + bpftool v7.5.0 from source → `~/dc-prefix`
- [x] host config yaml `etc/datacrumbs/configs/izzet-gdstrace-node.yaml` (sys_io custom + **cuFile uprobe**)
- [x] cmake configure + build + install datacrumbs → `~/dc-prefix`
- [x] cuFile probe category — auto-discovered `cuFileRead/ReadAsync/Write/WriteAsync/BatchIOSubmit/BatchIOGetStatus/HandleRegister`
- [x] **run on gdsio (GPU_DIRECT read) → `.pfw.gz` with 512 `cuFileRead` events + syscalls** ✅
- [x] capture cuFile **args (size/offset)** — each `cuFileRead` now has `args:{size,offset}` ✅
- [x] **block/NVMe kprobe** layer → cuFileRead↔NVMe amplification (1:1 aligned, 4× at 4 MiB) ✅
- [x] **DFAnalyzer** (`feat/datacrumbs`) → per-op cuFile↔NVMe attribution + 4× amplification ✅
- [ ] async/batch cuFile full arg extraction (kvikio/ESPN); map args.size → DFAnalyzer size (byte ampl.)

## ✅ (3) DFAnalyzer: per-op cross-layer attribution + amplification
Installed `izzet/dfanalyzer @ feat/datacrumbs` (venv `~/dfa-venv`; **pin `dftracer-utils==0.0.5`** —
0.0.9 renamed `Reader`→`TraceReader` and changed the Indexer/Reader API). Drove it via
`tools/dfa_drive.py` (`analyzer=datacrumbs preset=stack view_types=[proc_name,func_name]`) on the
gdsio `-i 4M` trace. The **stack preset nests events by time-containment per (pid,tid)**, so every
`nvme_setup_cmd` lands under the `cuFileRead` that issued it:
- per-LAYER: block=256, cufile=65, custom1(syscalls)=4738 events.
- **cross-layer: all 256 `nvme_setup_cmd` → `root_func = cuFileRead`.**
- **AMPLIFICATION: 64 cuFileRead → 256 NVMe cmds = 4.00× (mean 4.0/op, min 4, max 4).**

⇒ **GDSight = DataCrumbs (cuFile + NVMe plugins) → DFTracer trace → DFAnalyzer (stack hierarchy) →
per-op cross-layer GDS attribution + amplification.** No NVIDIA tool produces this.

Driving it surfaced + fixed **3 pandas≥2.2 bugs** in the `feat/datacrumbs` branch:
1. `assign_hierarchy` `KeyError 'pid'` — pandas≥2.2 drops groupby keys from the `apply` frame → carry
   pid/tid via a `_grp` column and restore them.
2. meta column-order mismatch from the restore → build the apply meta in matching order.
3. `set_stack_metrics` used the removed option `mode.use_inf_as_na` → no-op context + explicit inf→NA.

## ✅ (1)+(2) byte amplification + async + real reader (kvikio) end-to-end
- **(1) byte amplification:** patched the DataCrumbs reader (`dftracer.py io_function`) to map
  `args.size`/`args.offset` → the `size`/`offset` columns (it only read `size_sum`/`ret`). Sizes now
  flow: gdsio -i4M = 256 MiB cuFileRead = 256 MiB NVMe → **byte amplification 1.0×** (bytes conserved;
  the 4× is op-count). sys_io read/write bytes map too.
- **(2a) async:** `cuFileReadAsync` size via `size_t*` deref validated (gdsio -x5 → 128 events, size=1 MiB).
- **(2b) batch ✅:** `cuFileBatchIOSubmit` now **walks the `CUfileIOParams_t` array** (stride 64 B,
  size@+32, file_offset@+16; 256-cap BPF loop) → emits `args:{size=Σ sub-op bytes, count=nr, offset}`.
  Validated gdsio -x6: a 4-op batch shows `count=4, size=4 MiB`. (Added a `count` field to
  `cufile_event_t`.)
- **REAL READER (kvikio), end-to-end:** traced `step3_ragged` via `datacrumbs_wrap python`. kvikio uses
  **sync `cuFileRead`** (not async/batch as the libkvikio strings suggested), on a **worker thread** —
  all **3000** captured with per-op size+offset (the exact case the old LD_PRELOAD interposer missed:
  dlsym'd symbol + worker thread → vindicates eBPF-uprobe + the TGID fix). DFAnalyzer: **3013
  nvme_setup_cmd all → root `cuFileRead`**, 1601 MiB each way, amplification 1.0× (ragged ~546 KiB
  reads < 1.25 MiB MDTS → ~1 NVMe cmd each).

**Remaining:** batch `CUfileIOParams_t` array-walk (per-op size for cuFileBatchIOSubmit) + an ESPN build
to run that batch-based reader through the stack.

## ✅ (2) block/NVMe cross-layer correlation (the amplification metric)
Added a custom **block** plugin (`plugins/custom_probes/block/`, `event_type 5` → `get_data_5`) that
kprobes **`nvme_setup_cmd(ns, req)`** and emits a point event with
`args:{size=req->__data_len, sector=req->__sector}` (read via `BPF_CORE_READ`; `struct request` is in
vmlinux BTF). Picked `nvme_setup_cmd` via bpftrace: it ≈ the logical read count and runs **in the
submitting process context** (327/328 in `gdsio`), so DataCrumbs' TGID filter captures the worker-thread
GDS device commands. (`submit_bio` is noisier — counts other processes' I/O.)

**One trace now has both layers, same tids+timestamps:**
| workload | cuFileRead ops | nvme_setup_cmd | amplification |
|---|---:|---:|---|
| `gdsio -i 1M` (aligned) | 256 (1 MiB) | 256 (1 MiB) | **1.0×** (baseline) |
| `gdsio -i 4M` | 64 (4 MiB) | 256 (1310720/262144/… B) | **4.0×** (device MDTS ~1.25 MiB splits each read) |

This is the per-op cross-layer attribution (`cuFileRead{size,offset} → the N NVMe commands it became,
each with size/sector`) that **no NVIDIA tool produces**. DFAnalyzer correlates by tid + time window
to compute per-op amplification (op-count and bytes).

## ✅ (1) per-op cuFile arg capture (size/offset) — proper custom plugin
`gdsio -i 1M` → 512 `cuFileRead` events, all `args:{"size":1048576,"offset":<varies>}` + duration +
thread. Per-op GDS attribution by size+offset — the data for amplification/path analysis.

**Design — the right way (not a generic register hack).** First attempt captured `PT_REGS_PARM3/4` in
the *generic* uprobe path → **fragile** (would mislabel args for any other function/category, and wrong
for cuFile async/batch). **Reverted.** DataCrumbs' designed extension is a **custom probe plugin** with
explicit, signature-aware SEC programs — exactly how `sys_io` captures POSIX args
(`SEC("ksyscall/read") BPF_KSYSCALL(read_entry, int fd, void* data, u64 count)`). Added
`etc/datacrumbs/plugins/custom_probes/cufile/`:
- `cufile.bpf.c`: `SEC("uprobe/<libcufile>:cuFileRead") BPF_UPROBE(.., void* fh, void* buf, u64 size,
  u64 file_offset)` — typed args, only on functions whose signature we know. Entry stashes size/offset
  in a carry map (`cufile_args_map`); uretprobe emits a `cufile_event_t`. `cuFileReadAsync` derefs the
  `size_t*`; `cuFileBatchIOSubmit`/`cuFileHandleRegister` = duration-only for now.
- `cufile.bpf.h` (`cufile_event_t`), `cufile_process.h` (`get_data_4` → emits args), `probes.json`
  (function order = event_id order).
- config: 2nd custom category `cufile` (`event_type: 4`, `start_event_id: 200000`).

**Framework notes (how POSIX args are captured = the model):** custom plugins write their own SEC
programs; the generated `customN.bpf.c` just `#include`s the plugin; the generic uprobe generator does
duration-only. Processors dispatch `event->type → get_data_N` (`GET_DATA_N_EXISTS`, up to 10):
1=general, 2=sys_io, **3=usdt (taken)** → cuFile uses **4**. Kept the TGID worker-thread fix; reverted
the generic-path hack.

**Remaining:** full arg extraction for `cuFileReadAsync`/`cuFileBatchIOSubmit` (kvikio/ESPN use these).

**Gotcha:** after editing a shared BPF header, clean the BPF objects (stale BTF → `bpftool gen object`
`Invalid argument`).

## ✅ RESULT: per-op cuFile tracing works (with a fix)
`gdsio -w4 -x0` (509 GDS ops) → trace has **512 `cuFileRead`** events (`cat:cufile`, per-op `dur`
~0.8–1 ms ≈ GDS latency) + `cuFileHandleRegister`, alongside `openat/read/close` syscalls — one
DFTracer `.pfw.gz`. This is the per-op, multi-layer GDS trace no NVIDIA tool gives.

### The bug we found + fixed in DataCrumbs (upstream-worthy)
First run captured only `cuFileHandleRegister` (main thread), **not** the 509 `cuFileRead` (worker
threads). Root cause: the client lib registers `getpid()` and the BPF `pid_map` filter keyed by
**TID** (`pid_tgid & 0xFFFFFFFF`) — so only the registering thread + forked *child processes*
(`fork_exit` adds child pid) are traced; **pthread worker threads are missed**. Since essentially all
GDS workloads do I/O on worker threads (gdsio, kvikio threadpool, DALI), this drops the entire data
path. **Fix:** key `pid_map` by **TGID** (`pid_tgid >> 32`) in `need_tracing` + `init.bpf.c`
start/stop (kept `fn_pid_map` per-thread for entry/exit pairing). → all threads of a traced process
captured. *(Changes live in the `external/datacrumbs` submodule working tree — commit to a branch on
the izzet fork + PR.)*

## Build gotchas (for reproducing on the snapshot)
- libbpf apt is 1.3.0 (<1.5.0 required) and bpftool only ships for generic kernel, not `-nvidia` →
  build both from source (`libbpf/bpftool` repo @ v7.5.0/v1.5.0) into `~/dc-prefix`.
- deps: `clang llvm-dev libclang-dev libopenmpi-dev libyaml-cpp-dev libjson-c-dev libelf-dev zlib1g-dev`
  + `-DCMAKE_PREFIX_PATH=...;/usr/lib/llvm-18`.
- eBPF compile needs `asm/` headers: `sudo ln -sfn /usr/include/x86_64-linux-gnu/asm /usr/include/asm`.
- `DATACRUMBS_LAUNCHER_TYPE=NONE`/`OPENMPI` break (NONE wants 5 scheduler vars; OPENMPI writes an
  unquoted `--map-by ppr:` → bad env). **Use `SLURM`** (clean tokens; launcher unused for single-node).
- `DATACRUMBS_CONFIGURED_TRACE_DIR` must pre-exist at configure time.
- `datacrumbs_run` runs as **regular user** (it sudo's the server itself; needs passwordless sudo);
  set `cap_bpf,cap_perfmon,cap_sys_admin,cap_dac_read_search` on `$PREFIX/sbin/datacrumbs`; copy
  `libbpf.so.1.5.0`→`/usr/local/lib` + `ldconfig` so the root server finds it.
- **Apps must be instrumented** (`datacrumbs_track --executable <bin>` → patchelf `--add-needed`
  client lib) or wrapped — untracked processes produce empty traces. Traces land under `<trace_dir>/YY/MM/DD/`.
- Probe model: types 0=syscalls, 1=kernel kprobes (bio/ext4/iomap/fscache), 2=userspace uprobes,
  4=custom. cuFile = **type-2 uprobe** on `libcufile.so`. Output = DFTracer `.pfw.gz`.

## Overnight autonomous session (2026-06-08) — (1) tracer complete + (2) value proven
Finished the tracer and validated end-to-end on real + synthetic readers; full results +
cross-checks + pathologies in **`results/gdstrace-tracer-results.md`**.

**(1) tracer coverage — DONE:** sync `cuFileRead`/`cuFileWrite` (size/offset), async `cuFileReadAsync`
(pointer deref), **batch `cuFileBatchIOSubmit` (CUfileIOParams_t array-walk → count + Σsize)**, NVMe
`nvme_setup_cmd` (size/sector). Real readers through the full stack: **kvikio** (sync, dlsym'd,
worker-thread), **DALI** (sync, whole-file, 2.47× amplification), **ESPN** (batch reader, built from
`cufile_bread.cc`).

**(2) value — DONE:** cross-checked vs kernel nvidia-fs + bpftrace (same-run **exact** match; cuFile
layer 64=64/256MiB=256MiB, NVMe 259=259) and documented run-to-run device-split variance as the only
"drift". Four pathologies coarse tools miss, each diagnosed per-op: kvikio sub-16K POSIX path
divergence (gds_stats `posix=0`, blind), DALI device-cmd amplification (2.47×), unaligned byte
amplification (1.06×), ESPN per-read handle-registration (0.115s > 0.053s reads).

**Gotchas (overnight):**
- **`datacrumbs_wrap` does NOT inject into a plain C++ binary** (espn_bread captured nothing) — use
  `datacrumbs_track --executable <bin>` (patchelf). wrap worked for python; track is the reliable path.
- ESPN build: prebuilt `GDS` binary is buggy in our env (`device pointer already registered`); built
  `cufile_bread.cc` instead — needs `libssl-dev` (openssl/sha.h) and `-lcuda` (`cuGetErrorName`),
  `-I/usr/local/cuda-12.6/include` + `-lcufile -lcudart`.
- batch BPF array-walk: 256-iter cap on the loop (verifier); `CUfileIOParams_t` stride 64B,
  file_offset@+16, size@+32.

## nvidia-fs layer added (2026-06-08) — the missing middle (cuFile↔nvidia-fs↔NVMe complete)
New custom `nvidiafs` plugin (`event_type 6`) kprobes `nvidia-fs.ko`: `nvfs_io_start_op` (per-op driver
entry, 1 per cuFileRead = the bridge), `nvfs_get_p2p_dma_mapping` (TRUE zero-copy P2P), 
`nvfs_mgroup_pin_shadow_pages` (host shadow/bounce). gdsio -i1M: 256 cuFileRead → 256 nvfs_io_start_op
→ 320 p2p_mapping (4 shadow ≈0) → 324 nvme; DFAnalyzer shows cufile/nvidiafs/block as 3 layers, all
nested under cuFileRead. Per-op true-P2P-vs-bounce verdict now available. Finding: kvikio BufRegister=0
still does true P2P (p2p=3003/shadow=1) — no pre-registration ≠ bounce.

## nvfs_io as a DURATION + cross-layer TIMING via DFAnalyzer + bounce/compat demo (2026-06-08)
Upgraded the nvidiafs plugin: `nvfs_io_start_op`→`nvfs_io_complete` fire on the **same thread**, so
`nvfs_io` is now a **duration** op (not a point) nested under each cuFileRead → DFAnalyzer's
`compute_self_time` gives the cross-layer time breakdown. **All timing analysis is in DFAnalyzer**
(`tools/dfa_drive.py`), no custom .pfw parser (per user: analysis belongs in DFAnalyzer).
- GDS read: cuFileRead 0.338 s = **self 0.9% (cuFile/userspace) + child 99.1% (nvidia-fs+device via
  nvfs_io)**; overlap factor 3.97× (4 workers, 0 idle).
- **Bounce/compat induction:** reading a non-GDS mount (root ext4, no `data=ordered`) → cuFile compat;
  gdsio still prints `XferType: GPUD`, but GDSight shows `cuFileRead` with **0 nvfs_io** → `pread64`,
  kernel `nvidia-fs Reads` Δ = **0**, and **5.7× slower** per op (0.338→1.919 s). Unaligned `-U` does
  NOT bounce (still true P2P, byte-amplified only).
- DFAnalyzer fix: `fix_dtypes` swept `size_bin_*_mean` (fractional) into Int64 int_cols → crashed on the
  compat trace's 4 KiB-read bin (mean 9.5). Route bin `_mean`/`_std` to doubles. (analysis_utils.py)
- Unit gotcha: `_stack_traces.time` is **seconds** but `time_start/time_end` are **µs** — compute
  overlap from `time_start/time_end` only.

## First REAL workload: vLLM fastsafetensors GDS loader (2026-06-08)
Traced `--load-format fastsafetensors` (vLLM's GPUDirect weight loader) loading a 2 GiB safetensors
shard from /mnt/nvme1. `workloads/fastsafetensors_load.py` (GDS vs --nogds). Install: torch **cu126**
(NOT default cu130 — driver is 12.6) + safetensors + fastsafetensors into /opt/gds-venv.
- GDS: **1 cuFileRead + 1 cuFileHandleRegister** (register-once; opposite of DALI/ESPN churn) -> 131
  nvfs_io -> 1786 NVMe, true P2P, 2.65 GiB/s. Cross-checked: bpftrace=1 cuFileRead, kernel +2048 MiB.
- nogds: 0 cuFile, **2050 pread64** -> 2205 NVMe, 2.12 GiB/s (~25% slower). The GDS-vs-fallback contrast.
- cross-layer timing (DFAnalyzer): cuFileRead 2.7% cuFile/userspace + 97.3% below.
- **GOTCHA 1:** torch's pip wheels bundle their own libcufile (site-packages/nvidia/cufile/lib/
  libcufile.so.0); the system-libcufile uprobe misses it (kernel layers still fire). Fix: run with
  `LD_PRELOAD=$DATACRUMBS_CLIENT_LIB:/usr/local/cuda-12.6/.../libcufile.so.1.11.1` so the traced
  libcufile is the one loaded (both SONAME libcufile.so.0 -> ABI compatible).
- **GOTCHA 2 / limitation:** cuFile uses INTERNAL worker threads -> nvfs_io/NVMe land off the caller
  thread, so per-tid attribution splits (1319 NVMe->nvfs_io roots, 465->cuFileRead) and the nvfs_io
  start->complete duration (keyed {tid,event_id}) overlaps/not-additive. Robust signal = DFAnalyzer
  cuFileRead self/child. Future: cross-thread correlation by handle/op-id. dfa_drive.py now flags this.

## Cross-thread attribution FIXED: correlation id through the layers (2026-06-08)
fastsafetensors exposed that cuFile dispatches I/O to INTERNAL worker threads, so nvfs_io/NVMe land off
the caller thread -> per-tid time-containment attributed only 465/1817 NVMe to the cuFileRead (25.6%).
**Fix (all in OUR plugins, NO datacrumbs core changes):** shared header
`custom_probes/gdstrace_corr.bpf.h` owns 3 maps (defined in the cuFile plugin via GDSTRACE_CORR_OWNER,
extern'd in nvidiafs/block; all plugin .o link into one datacrumbs.bpf.o). cuFile op entry calls
`gdstrace_corr_begin(corr_id=entry_ts)` (per-tid map + per-tgid count + per-tgid op); device ops call
`gdstrace_corr_current()` = per-tid lookup (synchronous) else per-tgid op when count==1 (worker thread,
unambiguous). corr_id emitted as an arg on cuFile/nvfs_io/NVMe; DFAnalyzer reader (dftracer.py) maps
args.corr_id -> a column; dfa_drive.py attributes by corr_id.
- fastsafetensors: **1815/1817 NVMe -> cuFileRead = 99.9%** (was 25.6% by time-containment).
- gdsio (synchronous -w4): 98.5% by corr_id == 98.5% by time-containment (no regression).
- Trade-off: nvfs_io is now a POINT (its async start->complete duration was garbage); per-layer TIME
  decomposition dropped (cuFileRead latency still reported). Recovering it needs correct async per-layer
  durations. The user vetoed core changes -> kept everything in custom plugins (cuFile plugin owns maps).

## Real HF model (Qwen2.5-3B) + concurrent-loader limitation (2026-06-08)
Loaded a real 2-shard model (5.75 GiB, 434 tensors) via fastsafetensors_load.py --path <dir> (now
supports a model dir = all *.safetensors, vLLM-style). GDSight: 2 cuFileRead + 2 cuFileHandleRegister
-> 5168 NVMe, true P2P, 2584x amplification, 5886 MiB conserved, 2.78 GiB/s. Works on a genuine model.
LIMITATION: fastsafetensors loads shards CONCURRENTLY (2 overlapping cuFileReads + shared worker pool),
so corr_id attribution drops to ~36% (count>1 -> fallback refuses to guess which read a worker op serves;
unattributed, not misattributed). Robust fix = per-request GPU-buffer key (gpu_info->gpuvaddr from
nvfs_get_p2p_dma_mapping), but that's a non-BTF module struct at a hardcoded offset (brittle) -> DEFERRED
(user call). Process-level results (amplification/bytes/P2P) remain exact. Next: LMCache.

## LMCache GDS KV offload (writes+reads) traced (2026-06-08)
GDS writes confirmed STABLE on this node (tiny+256MB gdsio writes + LMCache KV writes; controller healthy,
0 dmesg errors -> the old controller drop was ACS, not GDS writes). Drove LMCache's GDS mechanism via
cufile-python (the module its GdsBackend uses; full backend blocked by lmcache 0.4.6's CUDA-13 c_ops /
libcudart.so.13 on our 12.6 node). workloads/lmcache_gds_kv.py: 4KB POSIX meta header + cuFileWrite at
offset 4096 (offload) + cuFileRead back (reload), like _save_gds/_load_gds. Separate venv ~/lmcache-venv
(torch cu126; lmcache pulls cufile-python+nixl+cupy-cuda13x -> kept out of the working gds-venv).
8x32MiB: 8 cuFileWrite + 8 cuFileRead + 16 cuFileHandleRegister -> 514 NVMe, corr_id 100%, 32x amp,
512MiB conserved. First GDS WRITE workload traced. Trace via LD_PRELOAD system libcufile (cufile-python
loads the bundled one, like fastsafetensors). dfa_drive.py now attributes cuFileWrite too (CUFILE_IO set).

## Byte amplification vs read size (the overhead of interest) (2026-06-08)
Distinguished device-CMD amplification (bytes conserved, ~free on this BW-bound drive, a per-op ratio
artifact) from BYTE amplification (device moves more bytes than requested = wasted bandwidth). gdsio -U
randread sweep, corr_id-clean (only nvme attributed to each cuFileRead -> exact per-op byte-amp):
4KiB=2.00x (8MiB req -> 16MiB device), 16KiB=1.27x, 64KiB=1.07x, 256KiB=1.02x, 1MiB=1.00x; 4KiB aligned
=1.00x. byte-amp ~= 1 + 4KiB_block_overhead/read_size. Invisible to gds_stats/throughput; worst exactly
in the small-read GDS regime (KV cache, embeddings). corr_id isolates each cuFileRead's own device bytes
for the exact number. Other axis (host CPU in nvfs_io/nvme submission path) negligible on this BW-bound
drive; would dominate on faster/IOPS-bound storage.

## Byte amplification in situ: embedding gather (real small-read workload) (2026-06-08)
workloads/embedding_gather.py (ESPN/DLRM/retrieval style: 4000 random fixed-size rows from a 4GiB table
-> GPU via cufile-python, table opened once + reused buffer). 768-dim fp32 rows = 3072B (unaligned):
BYTE-AMP = 2.000x exact (11.72MiB req -> 23.44MiB device), corr_id attribution 100%, register-once
(1 handleReg), true P2P (shadow~0) -> pure alignment waste at the device, not churn/staging. 1024-dim
(4096B aligned) = 1.000x. Exactly 2.000x because 3072=0.75*4096 -> half the rows straddle a 4KB block.
Invisible to gds_stats/throughput. Fix: pad rows to 4KiB multiple / coalesce. Demonstrates byte-amp on a
genuine workload, not gdsio -U.

## Rigor check on the byte-amp result (2026-06-08)
Validated the 2x byte-amp 4 independent ways: (1) first-principles 4KiB-block math per row size
{1024:4,2048:2,3072:2,4096:1,6144:1.33,8192:1}x matches measurement; (2) GDSight corr_id 2.000x;
(3) nvidia-fs readMiB 47/23.4=2.0x (oracle, independent of our kprobe); (4) /proc/diskstats 23.58/11.72
=2.0x (independent of tracer AND nvidia-fs). ALIGNED baseline = exactly 1.000x rules out readahead
(would inflate aligned) AND double-counting (would scale aligned to 2x). HONEST: the effect is textbook
O_DIRECT/4KiB-alignment (cuFile requires 4KiB-aligned offsets) - NOT a new phenomenon. Contribution =
automatic PER-OP attribution of a known-but-silent effect. Side-by-side: gds_stats/throughput see
requested only (blind); nvidia-fs/iostat see device total (aggregate, no per-op); GDSight = per-op
requested-vs-device exact. To detect it today you manually diff two counters from two layers + never get
per-op.

## Make-or-break: mixed retrieval, per-op attribution vs Nsight/gds_stats (2026-06-08)
workloads/mixed_retrieval.py: 4000 interleaved cuFile reads from table A (1024d/4096B aligned) + table B
(768d/3072B unaligned). Cross-checked all tools on the SAME run:
- gds_stats/nvidia-fs aggregate: 14 req -> 19 device = 1.36x BLENDED (can't localize).
- Nsight NVTX (nsys, profile.nvtx=true): 4000 cuFileRead ~142us lumped by name, NO device bytes -> blind.
- iostat/diskstats: ~19MiB device-wide, no attribution.
- GDSight + dfa_drive byte-amp-by-size: B(3072)=2.015x WASTE, A(4096)=1.000x -> CULPRIT PINPOINTED.
On-par check: our cuFileRead latency 131us ~= Nsight 142us (we add below-cuFile, don't lose cuFile view).
dfa_drive.py gained a "byte amplification BY cuFile op size" view (corr_id-attributed). Honest: effect
known (alignment); waste latent on BW-unsaturated drive (cost/scaling issue); size-keyed attribution
(same-size culprits need file/offset). nsys head-to-head confirmed libcufile NVTX (CUFILE_NVTX) gives
per-op cuFile but stops at the API (no nvfs/nvme).

## Saturation: byte-amp becomes a real ~2x throughput ceiling (grounded) (2026-06-08)
4KiB random GDS reads, aligned(packed) vs -U(unaligned), throughput vs concurrency:
-w16 1.45x, -w32 2.04x, -w64 2.5x (aligned 1.15 vs unaligned 0.46 GiB/s), -w128 2.07x. At saturation the
gap converges on the 2x byte-amp: device reads 512 vs 256 MiB (2x) for the same 256MiB requested, delivers
~2x less useful throughput. Fixing alignment ~doubles throughput on the SAME drive. Cross-check: at
saturation gds_stats/iostat show device busy + low useful -> wrong conclusion "buy faster storage";
GDSight shows byte-amp 2x -> "align/pack, ~2x free"; Nsight sees uniform per-op cuFile latency, no
device bytes. So the waste is LATENT at low load (earlier framing) but BITES at serving concurrency.
Grounded: DLRM-on-SSD (512B-of-4KB read amplification; FlashEmbedding APSys'21, arXiv:2110.11489) +
ESPN (arXiv:2312.05417, manually aligns embeddings 2 blocks->1 = the fix GDSight flags automatically).
Our 4KiB-unaligned = conservative 2x; documented sub-block embedding case up to 8x.

## CORRECTION (honesty): who can see the byte-amp discrepancy? (2026-06-08)
Re-checked: in the HOMOGENEOUS saturation run, existing tools DO see the aggregate 2x -- iostat device
1.18 GB/s vs app useful 0.59 GB/s = 2x; diskstats 4096 MiB device vs 2048 MiB requested = 2x. So
GDSight is NOT uniquely needed to DETECT the 2x in a uniform workload (an admin diffing iostat vs app
throughput catches it). (Earlier 'buy faster storage trap' framing overclaimed.) The UNIQUE value is
ATTRIBUTION in a MIXED workload (mixed_retrieval): iostat/gds_stats show one blended 1.36x and cannot say
which table; only per-op size/corr_id attribution pinpoints B=2.015x vs A=1.000x. Detection of aggregate
= existing tools; per-op/per-tensor attribution in heterogeneous workloads = GDSight. Docs corrected.

## Grounded real workload: ESPN multi-vector retrieval (2026-06-08)
Survey (actually searched, not from memory): runnable cuFile/GDS benchmarks are homogeneous (gdsio,
nixlbench); heterogeneous storage workloads (GIDS/BaM GNN) BYPASS cuFile (GPU-initiated NVMe) -> not our
stack. Real heterogeneous cuFile workload = ESPN (arXiv:2312.05417, published GDS retrieval). Grounded
params: CLS 128-dim fp16 = 256B + BOW ~2KB (32-dim fp16/token), 4KiB blocks, ~1000 docs/query, latency-
bound; authors packed CLS+BOW (2 blocks -> 1/doc). workloads/espn_retrieval.py (naive vs aligned over
dataset.bin). naive 3763 docs/s, 8000 ops, 31 MiB device; aligned 7646 docs/s (2.03x), 4000 ops, 15 MiB.
GDSight per-read-class: naive CLS(256B)=16.0x WASTE, BOW(2KB)=2.0x; aligned packed(2304B)=1.78x.
RIGOR cross-check: existing tools see the 2x AGGREGATE (docs/s app; nvidia-fs n= ops 2x; readMiB/iostat
device 2x; /proc/PID/io per-process; Nsight lumped). ONLY GDSight names the 256B CLS class as the 16x
redundant fix-target from the trace alone. Honest scope: automated per-class diagnosis (which class), not
revealing an invisible effect; ESPN already found it manually; CLS is obvious to an expert -> tool value
grows where the wasteful class is NON-OBVIOUS (still to be found). Also re-confirmed /proc/self/io
read_bytes DOES count GDS reads (300MiB) -> per-process attribution is NOT a gap (iotop sees it).

## Real workload: RAPIDS cuDF read_parquet -- HONEST NEGATIVE (2026-06-08)
cuDF 26.04 read_parquet (NYC taxi, 2.96M rows x 19 cols x 3 row-groups, 47.6MiB) via cuFile (GDS engages
even at LIBCUDF_CUFILE_POLICY=OFF; readMiB +48). workloads/cudf_read_parquet.py. GDSight: 58
per-column-chunk cuFileReads, heterogeneous 17KB-4MB (match parquet column-chunk sizes; >4MB sliced to
4MiB). Per-op byte-amp: small cols 1.1-1.4x, large ~1.0x; AGGREGATE ~1.0x (48=48 MiB). device-cmd amp
2.05x (MDTS, bytes conserved). corr_id 94% (cuDF thread-pool -> some concurrent reads -> count>1 ambig).
CONCLUSION: cuDF Parquet I/O is WELL-ENGINEERED -- no significant byte-amp pathology; small-column reads
waste a little but negligible aggregate. Tool correctly reports a clean bill of health. Pathology shows
in naive/hand-rolled code (ESPN-naive), not well-tuned libs -> tool value = diagnosing naive/misconfigured
GDS. (Rigor: report negatives.) NEXT: elbencho deep-dive (richer benchmark, can stress small-files/dir-trees).

## elbencho deep-dive (external/elbencho submodule) -- HONEST NEGATIVE (2026-06-08)
Built elbencho v3.1-6 with cufile/gds (deps: libboost-all-dev, libaio-dev). Submodule external/elbencho
(was wrongly cloned to ~/ first -> moved; convention: external tools under external/). Links SYSTEM
libcufile -> traceable with NO LD_PRELOAD trick (unlike torch/cudf bundled libcufile).
- --gds read of 4GB dataset.bin: 2970 MiB/s (drive ceiling), readMiB=4096=1.0x byte-amp (O_DIRECT aligned).
- Full GDSight (512MB, -b 1M -t4 --gds): 512 cuFileRead + 1 cuFileHandleRegister -> 512 nvfs_io ->
  768 p2p/nvme, corr_id attribution 100%, device-cmd amp 1.5x (MDTS), bytes conserved (512=512). Validates
  the tool on a standard 3rd-party benchmark, clean cross-check.
- bufreg test: --gds (bufreg) = true P2P (p2p 768, shadow 4); --cufile WITHOUT --gdsbufreg = 0 nvfs
  activity (silent compat/POSIX fallback -- registration required for GDS in elbencho).
CONCLUSION: elbencho is well-engineered (aligned, register-once) -> NO byte-amp pathology, like cuDF.
Two real tools now checked, both CLEAN. Pattern: pathology lives in naive/hand-rolled code (ESPN-naive),
not well-tuned libs. Tool correctly issues clean bills. Note: external/nixl + external/espn already exist.

## Edge-case hunt: hidden silent-degradation -- THREE NEGATIVES (stack is robust) (2026-06-08)
Aimed for layer-internal behaviors invisible to existing tools. Tested 3 hypotheses, all negative:
1. Silent bounce via BAR1 pressure: INFEASIBLE -- A100 BAR1=64GiB (>= GPU mem), can't exhaust.
2. Unaligned-write RMW: driver REJECTS unaligned O_DIRECT writes (gdsio -U -I3 wrote ~nothing, 0 reads)
   -> no silent read-modify-write edge case.
3. Managed-memory bounce (cudaMallocManaged + cuFile): TRUE P2P anyway (p2p=64, shadow~0, nvidia-fs
   +64 reads) -- same as cudaMalloc device buffer. No bounce. (workloads/managed_buffer_test.py)
CONCLUSION: modern GDS stack (A100/CUDA12.6/nvfs2.28/BAR1=64GiB/IOMMU-off) is ROBUST -- true zero-copy
P2P for device AND managed memory, rejects (not silently-degrades) unaligned writes. The nvfs shadow/
bounce path essentially never fires here. So the instrumented bounce-detection has nothing to flag on a
clean modern node. Hidden silent-degradation pathologies would need: older/misconfigured stack (IOMMU-on,
old GPU, small BAR1), REMOTE storage (NFS-RDMA/WekaFS/VAST where bounce/fallback is common), or a real
misconfigured deployment -- not this clean node. Reinforces: tool value = per-op attribution/verification
+ naive-code diagnosis; gotcha-finding needs a varied/real environment.

## Curated anti-pattern SUITE (motivating examples, decision-framed) (2026-06-08)
results/curated-pathologies.md + tools/run_curated_suite.sh: 3 realistic curated GDS anti-patterns where
the STANDARD-TOOL DECISION is wrong and per-op attribution gives the right fix.
- Case 1 'buy faster storage' (unaligned layout): gdsio 4K randread -w64 aligned 1.18 vs -U 0.43 GiB/s
  (~2.8x); iostat/gds_stats -> "buy faster drive"; GDSight byte-amp 2x -> "align data, 2.8x free".
- Case 2 'GDS is healthy' (kvikio 16KiB threshold): 4000 reads 60% small -> nvidia-fs n+1617 (large only);
  gds_stats "GDS working"; GDSight 1617 cuFileRead + 2385 pread64 -> "60% silently POSIX, batch them".
  workloads/kvikio_threshold.py.
- Case 3 'which tensor' (mixed 768d+1024d): aggregate 1.36x; GDSight per-read-class B(3072)=2.015x,
  A(4096)=1.000x -> "fix table B". workloads/mixed_retrieval.py.
HONEST: curated motivating examples on a robust modern stack -> prove the capability + the gap exists,
NOT demand (each effect is known to experts; the value is automatic per-op attribution -> the fix).
Removed mixed_tier.py (cufile-python errors on non-GDS files instead of compat -> pivoted Case 2 to
the real kvikio threshold). Cases 1+2 validated live; Case 3 = already-validated mixed_retrieval.

## Related-work survey for PDSW framing (2026-06-08) -> results/related-work.md
Read 4 nearest works. (A) Userspace cross-layer tracers (Recorder SC; DFTracer SC'24 [our group,
Yildirim co-author]; Darshan/DXT) -- trace HDF5->MPI-IO->POSIX, STOP AT POSIX, no kernel/device, mostly
NO mitigation. (B) eBPF kernel cross-layer (zns-tools: VFS->block->zone for ZNS SSDs, file->LBA->zone
correlation -- closest METHOD, but CPU/ZNS not GPU, by-LBA not by-app-op, STOPS at characterization;
IOscope). (C) GDS work (Ravi PDSW'20: built an HDF5 GDS VFD = mitigation/enabling + eval; ESPN: hand-
aligned embeddings = the fix we auto-detect; gds_stats/NVTX: aggregate/userspace-only). GAP: nobody does
per-op cross-layer attribution down the GDS path (cuFile<->nvidia-fs<->NVMe). ANGLE: (1) position as the
downward extension of DFTracer/DFAnalyzer into GDS kernel/device layers; (2) tool+characterization is an
accepted shape (Recorder/DFTracer/zns-tools stop there); (3) STRONGEST: don't stop -- close diagnosis->
fix loop (tool flags per-op pathology -> apply alignment/coalescing/batch -> measured ~2x win), like
Ravi's build-route but tool-guided. Lifts above "yet another tracer".

## Closed the diagnosis->fix loop for all 3 curated cases (tool-guided wins) (2026-06-08)
Per the related-work angle (most cross-layer tracers STOP at characterization; differentiate by closing
the loop). Tool flags per-op pathology -> apply the named fix -> measure:
- Case 1 (align data to 4KiB): unaligned 0.50 -> aligned 1.15 GiB/s = 2.3x throughput (gdsio -w64 4K).
- Case 2 (coalesce sub-16KiB to cross GDS threshold): 65536x4KiB POSIX 29 MiB/s (0 GDS ops) -> 4096x64KiB
  GDS 149 MiB/s (4096 GDS ops) = 5.1x (also fewer/larger ops; assumes batchable items). kvikio.
- Case 3 (pad 768d rows to 4KiB slots): 6029 -> 4194 B/row device = 1.44x less device BW/row (~1.4x at
  saturation). embedding_gather --pad (added).
Updated results/curated-pathologies.md with the fix-loop table + honest notes. This turns the suite from
characterization into diagnosis->fix->measured-win, which (per related-work.md) lifts it above the
stop-at-characterization norm (Recorder/DFTracer/zns-tools) toward Ravi's build-route but tool-guided.

## LBA-matching prototype: deterministic cross-layer attribution by device address (2026-06-08)
tools/lba_match.py: build file-offset<->LBA map from the file extent map (filefrag; fs on raw nvme1n1,
device_sector=phys_fs_block*8), then match each NVMe cmd's sector -> the cuFileRead that requested that
file range. Address-based, so independent of thread/timing.
VALIDATION (agreement where corr_id assigns): fastsafetensors 1815/1815 = 100%; kvikio all-64K 97.2%
(2.8% gap = same-range collisions LBA can't disambiguate but corr_id timing can); concurrent 8-thread 93%.
HONEST KEY FINDING: corr_id is ROBUST across ALL sync cases I can generate -- single 100%, 8-thread x 4000
concurrent 99.9%, fastsafetensors single/large 99.8%. The per-tid+count fix handles synchronous concurrency
far better than the historical "36%" implied. The corr_id drop only occurs on the ASYNC / cuFile-internal-
worker-pool path (kvikio raw_read_async = cuFileReadAsync), which SEGFAULTS under datacrumbs (tracer
limitation, workloads/async_reader.py) -- so the dramatic "corr_id drops -> LBA recovers" demo is currently
BLOCKED by the async-tracing crash, not by LBA.
NET (honest contribution): corr_id (timing/thread heuristic) and LBA (deterministic address) are
COMPLEMENTARY and MUTUALLY VALIDATING -- corr_id disambiguates same-range reads via timing; LBA
disambiguates thread-decoupled/async via address; 100% agreement where both apply proves the cheap
always-on heuristic CORRECT (not just plausible), which heuristic-only tracers can't offer.
OPERATIONAL BUG: crashed datacrumbs runs leave stale servers; multiple servers collide on the same eBPF
probes -> empty/corrupt traces. Must `sudo pkill -9 -f datacrumbs` between runs / after a crash.
workloads/{concurrent_reader,async_reader}.py added.

## METHOD RESULT: corr_id collapses on async, LBA recovers -- complementary framework (2026-06-08)
Fixed lba_match.py to parse cuFileReadAsync (not just cuFileRead). Clean async traces (the "async crash"
was the server pileup, not async). Results -> results/cross-layer-attribution.md:
- ASYNC cuFileReadAsync: corr_id 6.4% (200 ops) / 8.5% (2000 ops) -- COLLAPSES (nvme carries no corr_id,
  fully decoupled; of the few it assigns only ~37% correct). LBA 98.5% / 97.2% -- RECOVERS by device address.
- SYNC: corr_id robust 99.8-99.9% (all cases incl 8-thread); LBA good but same-range ambiguity hurts on
  large-reads-over-small-file (8x4MB -> LBA 47.8%).
- Where both apply they AGREE ~100% (fastsafetensors 1815/1815) -> mutual validation.
CONTRIBUTION (method): complementary cross-layer attribution framework -- corr_id (timing/thread, cheap,
sync-robust) + LBA (deterministic address, async-robust), each covers the other's failure mode. Per-op
attribution of ASYNC cuFile I/O below the API (inference-relevant) appears unique. This is the demonstrated
core design contribution, no longer just "yet another tracer".

## Cross-examined the REAL engine: NVIDIA NIXL (Dynamo transfer library) (2026-06-08)
Built/ran NIXL GDS backend (nixl-cu12 1.2.0 wheel; default `nixl`=cu13 wheel is incompatible with our 12.6
driver -> use nixl-cu12; import as nixl_cu12). workloads/nixl_gds_read.py: file->VRAM via NIXL GDS, async.
Findings:
- NIXL's default GDS backend uses the cuFile BATCH API (cuFileBatchIOSetUp/Submit/GetStatus; symbol scan);
  GDS_MT uses cuFileRead/Write. Real engines drive GDS via batch/async, not plain cuFileRead.
- GDSight observes it END-TO-END: 2000 cuFileBatchIOSubmit -> 2000 nvfs_io (true P2P) -> 2003 NVMe.
  Per-op attribution: corr_id 99.9%, LBA 97.2%, agreement 97.3% -> real engine attributed, validated 2 ways.
  -> results/cross-layer-attribution.md (NIXL row + section).
GOTCHAS (for the partner/repro): (1) nixl-cu12 wheel, not `nixl` (cu13). (2) NIXL resolves cuFile from
torch's BUNDLED libcufile -> need LD_PRELOAD system libcufile so the cuFile uprobe fires (kernel kprobes
fire regardless). (3) NIXL GDS batch caps concurrency: --inflight 32 -> NIXL_ERR_BACKEND, <=16 ok (this,
not a tracer conflict, caused earlier failures). (4) datacrumbs client does NOT conflict with NIXL.
(5) corr_id collapse is the high-in-flight cuFileReadAsync regime; NIXL's count=1 batches don't trigger it.
Server-pileup gotcha persists: pkill must use a var-built pattern ("sbin/${p} run") so the literal isn't in
my own cmdline (else self-kill).

## DeepNVMe (DeepSpeed) Phase 1: trace the GDS path -- WORKS (2026-06-09)
Install: deepspeed-venv; torch cu126 FIRST, then `DS_BUILD_GDS=1 DS_BUILD_AIO=1 pip install deepspeed
--no-build-isolation` w/ CUDA_HOME=/usr/local/cuda-12.6 (pip build isolation hides torch -> MUST use
--no-build-isolation, else "Unable to pre-compile async_io, please first install torch"). ds_report:
async_io [OKAY], gds [OKAY]. DeepSpeedExamples submodule (external/deepspeedexamples); deepnvme/file_access/
has paired py_/aio_/gds_ load+store scripts (clean A/B + POSIX baseline).
Phase 1 trace (gds_load_gpu_tensor.py, 1GB, LD_PRELOAD system libcufile so the uprobe fires -- DeepNVMe
resolves cuFile via torch's bundled lib otherwise): DeepNVMe's GDS op issues ONE plain cuFileRead
(sync_pread of the whole file, on a worker thread tid!=pid) -> driver splits by block_size(1MiB) into
1024 nvfs_get_p2p_dma_mapping (true P2P) + 1024 nvme_setup_cmd; 4 nvfs_io; readMiB+1024 (true GDS, 2.7 GB/s).
Attribution: corr_id 1024/1024 + LBA 1024/1024, agree 100% (1 op in flight -> count==1 fallback).
KEY: DeepNVMe uses PLAIN cuFileRead (not the batch API like NIXL); block_size sets the device-cmd count.
Microbench = 1 cuFileRead/op so corr_id always wins; the corr_id-vs-LBA divergence needs MANY concurrent
reads (ZeRO-Inference, Phase 3). NEXT: Phase 2 knob sweep (block_size/queue_depth/single_submit/
overlap_events/intra_op_parallelism) -> per-config device pattern (explain-the-autotuner) + GDS-vs-AIO A/B.

## DeepNVMe Phase 2: explain-the-autotuner + GDS-vs-AIO (2026-06-09) -> results/deepnvme.md
GDS-vs-AIO A/B (same API): GDS=1024 p2p (true zero-copy), AIO=0 p2p (CPU bounce, libaio->host->cudaMemcpy);
same device bytes, only GDSight shows which is actually P2P vs host-staged. block_size sweep (1GB):
256K->4101 nvme/2.46 GB/s; >=1M->~1024 nvme/2.87 GB/s (saturates; DeepNVMe caps chunks at ~MDTS). Throughput
gated by device-command count, which block_size controls to a ~1MiB/MDTS floor -> explains why the autotuner
picks ~1MiB (device-level mechanism, invisible to throughput-only tuning). intra_op_parallelism 1 vs 8: no
effect (device-bound single read; honest no-op). workloads/deepnvme_gds_load.py. NEXT: Phase 3 ZeRO-Inference
(many concurrent param reads -> the corr_id-vs-LBA divergence on a real workload).

## DeepNVMe Phase 3: ZeRO-Inference real workload (2026-06-09) -> results/deepnvme.md
OPT-1.3B + DeepSpeed ZeRO-3 --disk-offload --use_gds (weights->NVMe, streamed to GPU via GDS each forward).
Generate streamed +94.5 GB via ~9800 GDS reads. Traced (bounded): 350 cuFileRead + 100 cuFileWrite ->
25864 p2p (true P2P) + 25864 nvme; per-op corr_id attribution 98.6% (25494/25864). DeepNVMe uses SYNC
cuFileRead -> per-tid corr_id resolves the concurrent layer-prefetch (cheap heuristic suffices; LBA's
async advantage NOT triggered -- DeepNVMe is sync, not cuFileReadAsync). Each cuFileRead = a ~196MB param
tensor -> ~74 device cmds, each tied to its read. GOTCHAS: --offload-dir MUST be on /mnt/nvme1
(data=ordered); example uses old transformers fork -> patched tokenizer.batch_encode_plus->tokenizer();
single-pass corr_id count under-reports (nvme precede the cuFileRead EXIT event in file order -> two-pass).
NET (Phases 1-3): trace GDS path + explain autotuner (block_size->device-cmd->MDTS) + GDS-vs-AIO bounce
detection + real ZeRO-Inference per-op attribution 98.6%. Honest: DeepNVMe clean+sync -> real-workload
attribution + explained tuning, NOT a pathology/LBA-win.

## DeepNVMe cross-check: current tools vs GDSight (measured) (2026-06-09)
Ran the standard tools side-by-side (was missing). nvidia-fs(GDS aggregate) + diskstats(device, =iostat):
- GDS vs AIO: GDS readMiB +5120 / AIO +0 (nvidia-fs DISTINGUISHES); diskstats IDENTICAL 5125 IOs / 5120 MiB
  for both (iostat BLIND to P2P-vs-bounce).
- block_size: nvidia-fs SAME (3 cuFile reads, +3072 MiB) for 256K and 4M (BLIND to device-cmd count);
  diskstats device-IOs 12293(256K) vs 3077(4M) (iostat SEES the count).
=> each aggregate tool sees ONE axis, blind to the other (nvidia-fs: GDS-engaged; iostat: device-cmd count);
NEITHER attributes per-op. GDSight spans both axes + ties to the causing op (P3: 350 reads->25864 cmds 98.6%).
HONEST CORRECTION to Phase 2a wording: nvidia-fs aggregate ALREADY distinguishes GDS-vs-bounce for separate
runs; our unique value there is PER-OP in a MIXED run, not the distinction itself. -> results/deepnvme.md.
REMINDER: always run current-tools cross-check alongside our tool (the be-rigorous rule) -- it both tempers
overclaims and sharpens the contribution (here: the two-axis blindness of the aggregates).

## NIXL cross-check backfill: current tools vs GDSight (measured) (2026-06-09)
Ran nvidia-fs(GDS aggregate)+diskstats(device) on the NIXL GDS workload (2000x64KiB, inflight 8):
- nvidia-fs: 2000 GDS reads, +125 MiB. iostat/diskstats: +2008 device IOs, +125 MiB. (64KiB ~= 1 cmd each.)
- ours (from trace): 2000 cuFileBatchIOSubmit -> 2003 device cmds, corr_id 99.9% + LBA 97.2%.
=> aggregates give totals; NEITHER ties a device command to a logical transfer. For NIXL (async/concurrent
engine) that gap is sharper than DeepNVMe: when many KV-transfers overlap, "which transfer caused this
command/latency" is unanswerable from aggregates -> ours per-op (LBA = deterministic backup for the
decoupled path). No block_size axis here (64KiB ~= 1 cmd); NIXL's distinctive axis = async per-op attribution.
-> results/cross-layer-attribution.md (NIXL cross-check table). Now both real engines (NIXL, DeepNVMe) carry
the current-tools cross-check symmetrically.

## cuCIM WSI: a REAL non-obvious pathology in the wild (2026-06-09) -> results/cucim.md
Digital-pathology whole-slide-image tiled reads = NVIDIA's flagship cuCIM+GDS example. Real Aperio SVS
(CMU-1.svs, 23220 JPEG tiles 256x256, mean 6.7KB/tile, range 2.3-33.7KB), read via kvikio (cuCIM's
gds_whole_slide path). workloads/cucim_gds_tiles.py. FINDING: 4000 random tiles -> 712 cuFileRead (GDS,
tiles>=16KB) + 3289 pread64 (silent POSIX, tiles<16KB) = 82% of WSI tiles SILENTLY BYPASS GDS (below
kvikio's 16KB threshold). Non-obvious: WSI is the GDS marketing workload but tile size defeats it.
CROSS-CHECK: gds_stats/cuFileGetStats sees 712 cuFile reads -> "GDS healthy" (BLIND: the 3289 POSIX reads
never enter cuFile); nvidia-fs readMiB +19 (GDS tiles only, BLIND to POSIX); diskstats +3788 device IOs
(both paths, can't split). ONLY GDSight (traces cuFileRead AND pread64 per-tile) names the 82% bypass.
FIX: coalesce tiles to >=16KB (recommended) OR KVIKIO_GDS_THRESHOLD=0 (all GDS but 25.5->42 MiB = 1.65x
byte-amp). This is the demand proof: a flagship GDS workload where GDS silently doesn't apply to 82% of I/O
and the GDS health tool says fine. Caveat: kvikio threshold path (cuCIM's benchmark path); read_region
(device=cuda) HUNG separately. read_region hang worth its own look.

## cuCIM finding -- HONEST NUANCE (forcing GDS is worse) (2026-06-09)
Rigor check: forcing GDS (KVIKIO_GDS_THRESHOLD=0) was SLOWER (0.82 vs 0.75s) + 1.65x byte-amp -> the 82%
bypass is LARGELY CORRECT (GDS doesn't help sub-16KB tiles; that's why the threshold exists). So the finding
is NOT "flip a flag". It is: (1) the flagship GDS workload barely uses GDS (only 18% of tiles), and gds_stats
can't tell you (sees 712 reads -> healthy); (2) to actually benefit from GDS on WSI you must RESTRUCTURE
(coalesce tiles to >=16KB), not flip the threshold. GDSight reveals the gap + the real lever (per-tile
sizes); standard tools blind. Softened "pathology" -> "GDS-vs-reality gap / under-delivery". Tempered the
doc title + fix sections. This is honest demand: a recognized workload where GDS silently under-delivers and
tools are blind to why -- but the fix is restructuring, and forcing GDS is counter-productive.

## cuCIM lever VERIFIED: coalesce -> 19.5x for sequential access (2026-06-09)
workloads/cucim_coalesce_test.py: 4000 contiguous tiles (0% gap), per-tile (4000 small reads, mostly POSIX)
0.395s/+20MiB GDS vs coalesced 1MiB GDS reads (26 reads) 0.020s/+26MiB GDS = 19.5x speedup + true GDS.
Honest: speedup combines fewer/larger ops + GDS engagement (both from restructure); SEQUENTIAL only (random
patch gather can't coalesce -> GDS genuinely under-delivers there). So the cuCIM default per-tile path leaves
19.5x on the table for region scans, and GDSight shows why (small POSIX reads vs coalesced GDS). NEXT:
investigate read_region(device=cuda) hang, then update GDS-TRACE-PITCH.md.

## read_region investigation -> CORRECTS the cuCIM finding (2026-06-09)
read_region(device=cuda) does NOT persistently hang (the 256s was a cold first-GDS-init transient; warm =
0.9s for 4000 patches; small cases work in 0.35s). Bisection: all cuda configs work fast once warm.
CRUCIAL CORRECTION: read_region(device=cuda) 4000 patches -> 108 nvfs_io (GDS) + 1 pread64 = it COALESCES
tiles into ~1MiB GDS reads, NO per-tile bypass. So the PRODUCTION cuCIM GDS API is well-engineered/clean.
The 82% bypass is the NAIVE per-tile kvikio path (cuCIM's gds_whole_slide BENCHMARK + naive users), NOT the
main API. Tempered cucim.md: naive per-tile under-delivers (82% bypass, 19.5x slower than coalesced);
production read_region coalesces (clean). Consistent with "well-engineered clean, naive under-delivers".
Tool value: reveals which path your code is on (per-tile bypass vs coalesced GDS) + the 19.5x lever, which
gds_stats/iostat can't. workloads/read_region_test.py. NEXT: update GDS-TRACE-PITCH.md.

## Tracer overhead measured (Table C / §5.x) + rebuild on v3 (2026-06-24)
Rebuilt the full DataCrumbs toolchain on a fresh v3 instance (the image predates the tracer work):
libbpf 1.5.0 + bpftool 7.5.0 from source → `~/dc-prefix`; `cmake -G Ninja` with
`-DBPFTOOL_EXECUTABLE -DDATACRUMBS_HOST=izzet-gdstrace-node -DDATACRUMBS_LAUNCHER_TYPE=SLURM
-DDATACRUMBS_CONFIGURED_TRACE_DIR=<must-exist>`; `ninja datacrumbs_explorer datacrumbs_generator`
→ `ninja run_explorer run_generator` (emits per-host BPF sources: custom1/cufile/block/nvidiafs)
→ `ninja && ninja install`; caps on `$PREFIX/sbin/datacrumbs`; libbpf.so → /usr/local/lib + ldconfig;
DFAnalyzer venv `~/dfa-venv` — **install dfanalyzer NON-editable** (editable shadows the `dftracer`
namespace so `dftracer.utils` from `dftracer-utils==0.0.5` isn't found). Scripted: `chameleon/build_datacrumbs.sh`.

**Verified probes attach + fire** (the capture half is fine): `bpftool prog show` → `read_entry`
run_cnt 17k+, `openat` 9k; `bpftool link show` → 10 uprobes on `libcufile.so`, nvme/nvfs kprobes.

**Orchestration bugs hit (datacrumbs_run wrapper, NOT capture):**
- `/var/run/datacrumbs/datacrumbs.runid` is **sticky** → consecutive runs reuse the same run_id and
  collide on the per-run `/tmp/datacrumbs_cc_<id>.log`. Must clear runid + stale server between runs.
- The stop path (`set -eo pipefail`) appends to that log; a **"Permission denied" on the log trips
  `set -e` and aborts graceful stop → server hard-killed before flush → 0-byte `.pfw.gz`.**
  ⇒ **trace-write is currently broken**; blocks the cross-layer *figures*, but NOT the overhead numbers.
- `dc_reset` (kill server + `rm /var/run/datacrumbs/*` + `/tmp/datacrumbs_*.log`) before each run is the workaround.

**Overhead result — final (size × {seq,rand} sweep, x4 sizes 4K–4M, + achieved-IOPS column):**
rand 256M/N5 (tight, CV<1.3%); seq re-run 2G/N7 to lengthen short runs. Key finding: **overhead
collapses onto achieved IOPS** — seq & rand on one line, fit ≈0.22%/kIOPS ⇒ **~2.2 µs/op** (matches
bpftime kernel-uprobe ~3.2µs, amortized → self-validating). ≈0% at 1M/4M (both patterns); rises to
8% (rand 4K, 36.8 kIOPS) / 15% (seq 4K, 58.8 kIOPS). **Sequential 4K–64K stayed noisy even at 2G
(CV 8–16%) — intrinsic mid-size-seq variance on this single PM983 (baseline CV ≈ traced CV, NOT a
tracer effect); those cells have wide error bars but lie on the IOPS curve.** Harness
`tools/overhead_bench.sh` (PATTERNS/S/N/TAG env), merge+IOPS `tools/overhead_table.py`; data in
`results/step5-overhead/{overhead_final.txt,overhead_raw_*}`; written up in
`results/instrumentation-cost-coverage.md` Table C.

## ✅ Trace-flush bug FIXED (2026-06-24) — empty .pfw.gz root cause + fix
**Symptom:** every traced run produced a **0-byte `.pfw.gz`** on the v3 rebuild, despite probes
firing (verified: `pid_map` has the app pid, `cuFileRead_entry` run_cnt=65536, events in `fn_pid_map`).
So **capture worked; the server just never wrote the trace.**
**Root cause (two coupled bugs):**
1. **`datacrumbs_stop` did `kill -9` (SIGKILL).** The server flushes its trace **only on SIGINT**
   (binary strings: `Received SIGINT … exiting gracefully` + `EventProcessor::finalize` + `fflush`).
   SIGKILL → no finalize → empty trace.
2. The stop path's log redirect (`>>$LOG`) hit a (cosmetic) "Permission denied" and, under
   `set -eo pipefail`, **aborted the stop function before the graceful step ran**.
**Fix (in `external/datacrumbs` fork @ feat/cufile-gds, commit e817c8e):**
- `datacrumbs_stop.in`: **SIGINT first → wait for graceful exit → SIGKILL stragglers**; matcher
  `[d]atacrumbs` so it no longer kills unrelated shells (that was the "Killed/no output" noise).
- `datacrumbs_utility.in`: ensure per-run log is writable + make start/stop log redirects non-fatal
  (`|| true`) so the graceful stop is always reached.
**Verified end-to-end:** gdsio 4K randread → **3.87 MB trace, 65535 cuFileRead → 65534 nvme_setup_cmd
+ 65535 nvfs_io + 65534 p2p_dma_mapping**; parses through DFAnalyzer (per-LAYER cufile/block/nvidiafs,
per-op cross-layer attribution, 1.0× amplification, true-P2P). The installed `~/dc-prefix` scripts
regenerate from the patched `.in` via `ninja install`. Residual: a harmless cosmetic "Permission
denied" on the per-run log line (does not affect the trace). **TODO: push the fork commit + PR.**

## Trace-all mode unlocks unmodified-workload tracing (2026-06-25)
`datacrumbs_wrap` (LD_PRELOAD of the client lib) SEGFAULTS on the kvikio/cupy python stack, so real
python GDS workloads couldn't be traced via injection. Fix: rebuild with
`-DDATACRUMBS_TRACE_ALL_PROCESSES_OPT=ON` → BPF `need_tracing()` returns 1 for all PIDs (no pid_map
filter, no injection needed) → trace UNMODIFIED python+kvikio cleanly. Confirmed kvikio uses the
SYSTEM libcufile we uprobe (cuFileRead/Async events captured). Build gotcha: the default `ninja`
target races a "clean BPF artifacts" step that deletes datacrumbs.bpf.o before install; workaround =
`ninja <...>/objects/datacrumbs.bpf.o` directly, then `cmake -P build/cmake_install.cmake` (no ninja),
then re-setcap. Two tracer modes now: pid-filter (default, targeted, low overhead) vs trace-all
(unmodified processes; filter the trace by the workload's file extents / pid). Used for the real
kvikio RAG-mix necessity result (results/xlayer/necessity.md Result 2).

## corr_id fallback made sound (XOR) + rebuild-drops-trace-all reminder (2026-07-11)
External review flagged the process-level corr_id fallback as unsound: when a device command fires
off-thread it read `proc[tgid]` (a plain last-writer), but `cnt[tgid]==1` does NOT imply proc holds the
surviving op (an op that entered+exited during another's flight overwrites it). Fix in
`plugins/custom_probes/gdstrace_corr.bpf.h`: replace proc with `cufile_active_xor[tgid]`, the XOR of all
active ids, toggled on begin/exit -> when count==1 the XOR equals the sole active op's id (sound under
interleave). Sync (active_op[tid]) unaffected. Oracle re-run IDENTICAL (sync 100%, async corr_id 1.6% /
LBA 100%), so the fix is sound-not-number-moving. **Rebuild reminder (re-hit today):** a plain rebuild
defaults `-DDATACRUMBS_TRACE_ALL_PROCESSES_OPT=OFF` and SILENTLY drops trace-all (kvikio capture returns
empty 23-byte traces while pid-filter/oracle still work) -> always pass `...=ON`, then re-run
`chameleon/setup_datacrumbs_runtime.sh` to re-setcap. Also: never pipe `datacrumbs_run` through
head/grep (SIGPIPE kills it before it flushes); redirect to a file (setsid + `> log 2>&1 < /dev/null`).

## POSIX pread offset capture for per-op attribution (2026-07-11)
Added a signature-aware `SEC("uprobe/libc:pread")` to the cufile plugin (cufile.bpf.c) capturing
(count, offset) via a NON-corr entry/exit (POSIX bypasses cuFile, so it must not register a corr_id),
emitting a `cufile_event_t` (type 4 -> get_data_4 already prints offset). Event named via cufile
probes.json `functions[5]` -> `start_event_id`+5 = 200005. Four gotchas hit, in order:
1. **BPF symbol collision:** the program name must be globally unique across plugins. sys_io generates a
   `pread64_entry`; naming mine `pread64_entry` too -> `bpftool gen object: conflicting non-weak symbol`.
   Renamed to `pread_gds_entry/exit`.
2. **Explorer name-dedup:** the probe explorer skips a duplicate function name across plugins ("Function
   name 'pread64' already processed"). sys_io owns `pread64`; use the libc alias **`pread`** (same address
   as pread64/__pread64) so the name is unique.
3. **bpf.o clean-race** (known): the default `ninja -C build` zeroes datacrumbs.bpf.o. Workaround that
   worked: build component `.o`s, then link manually -- `bpftool gen object out.bpf.o common.o init.o
   custom1.o cufile.o block.o nvidiafs.o` -- and `cp` it over the installed one.
4. **category map not installed:** events fired but dropped with `No category found for event_id 200005`.
   The generated `build/data/{categories,probes}-<host>.json` must be copied to
   `~/dc-prefix/etc/datacrumbs/data/`. After that, `pread` events carry offset.
Result: address attributes all 2000 POSIX device commands to their exact pread (results/xlayer/posix_addr_attr.txt).
