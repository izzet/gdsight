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

⇒ **GDS-Trace = DataCrumbs (cuFile + NVMe plugins) → DFTracer trace → DFAnalyzer (stack hierarchy) →
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
  gdsio still prints `XferType: GPUD`, but GDS-Trace shows `cuFileRead` with **0 nvfs_io** → `pread64`,
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
supports a model dir = all *.safetensors, vLLM-style). GDS-Trace: 2 cuFileRead + 2 cuFileHandleRegister
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
{1024:4,2048:2,3072:2,4096:1,6144:1.33,8192:1}x matches measurement; (2) GDS-Trace corr_id 2.000x;
(3) nvidia-fs readMiB 47/23.4=2.0x (oracle, independent of our kprobe); (4) /proc/diskstats 23.58/11.72
=2.0x (independent of tracer AND nvidia-fs). ALIGNED baseline = exactly 1.000x rules out readahead
(would inflate aligned) AND double-counting (would scale aligned to 2x). HONEST: the effect is textbook
O_DIRECT/4KiB-alignment (cuFile requires 4KiB-aligned offsets) - NOT a new phenomenon. Contribution =
automatic PER-OP attribution of a known-but-silent effect. Side-by-side: gds_stats/throughput see
requested only (blind); nvidia-fs/iostat see device total (aggregate, no per-op); GDS-Trace = per-op
requested-vs-device exact. To detect it today you manually diff two counters from two layers + never get
per-op.

## Make-or-break: mixed retrieval, per-op attribution vs Nsight/gds_stats (2026-06-08)
workloads/mixed_retrieval.py: 4000 interleaved cuFile reads from table A (1024d/4096B aligned) + table B
(768d/3072B unaligned). Cross-checked all tools on the SAME run:
- gds_stats/nvidia-fs aggregate: 14 req -> 19 device = 1.36x BLENDED (can't localize).
- Nsight NVTX (nsys, profile.nvtx=true): 4000 cuFileRead ~142us lumped by name, NO device bytes -> blind.
- iostat/diskstats: ~19MiB device-wide, no attribution.
- GDS-Trace + dfa_drive byte-amp-by-size: B(3072)=2.015x WASTE, A(4096)=1.000x -> CULPRIT PINPOINTED.
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
GDS-Trace shows byte-amp 2x -> "align/pack, ~2x free"; Nsight sees uniform per-op cuFile latency, no
device bytes. So the waste is LATENT at low load (earlier framing) but BITES at serving concurrency.
Grounded: DLRM-on-SSD (512B-of-4KB read amplification; FlashEmbedding APSys'21, arXiv:2110.11489) +
ESPN (arXiv:2312.05417, manually aligns embeddings 2 blocks->1 = the fix GDS-Trace flags automatically).
Our 4KiB-unaligned = conservative 2x; documented sub-block embedding case up to 8x.

## CORRECTION (honesty): who can see the byte-amp discrepancy? (2026-06-08)
Re-checked: in the HOMOGENEOUS saturation run, existing tools DO see the aggregate 2x -- iostat device
1.18 GB/s vs app useful 0.59 GB/s = 2x; diskstats 4096 MiB device vs 2048 MiB requested = 2x. So
GDS-Trace is NOT uniquely needed to DETECT the 2x in a uniform workload (an admin diffing iostat vs app
throughput catches it). (Earlier 'buy faster storage trap' framing overclaimed.) The UNIQUE value is
ATTRIBUTION in a MIXED workload (mixed_retrieval): iostat/gds_stats show one blended 1.36x and cannot say
which table; only per-op size/corr_id attribution pinpoints B=2.015x vs A=1.000x. Detection of aggregate
= existing tools; per-op/per-tensor attribution in heterogeneous workloads = GDS-Trace. Docs corrected.

## Grounded real workload: ESPN multi-vector retrieval (2026-06-08)
Survey (actually searched, not from memory): runnable cuFile/GDS benchmarks are homogeneous (gdsio,
nixlbench); heterogeneous storage workloads (GIDS/BaM GNN) BYPASS cuFile (GPU-initiated NVMe) -> not our
stack. Real heterogeneous cuFile workload = ESPN (arXiv:2312.05417, published GDS retrieval). Grounded
params: CLS 128-dim fp16 = 256B + BOW ~2KB (32-dim fp16/token), 4KiB blocks, ~1000 docs/query, latency-
bound; authors packed CLS+BOW (2 blocks -> 1/doc). workloads/espn_retrieval.py (naive vs aligned over
dataset.bin). naive 3763 docs/s, 8000 ops, 31 MiB device; aligned 7646 docs/s (2.03x), 4000 ops, 15 MiB.
GDS-Trace per-read-class: naive CLS(256B)=16.0x WASTE, BOW(2KB)=2.0x; aligned packed(2304B)=1.78x.
RIGOR cross-check: existing tools see the 2x AGGREGATE (docs/s app; nvidia-fs n= ops 2x; readMiB/iostat
device 2x; /proc/PID/io per-process; Nsight lumped). ONLY GDS-Trace names the 256B CLS class as the 16x
redundant fix-target from the trace alone. Honest scope: automated per-class diagnosis (which class), not
revealing an invisible effect; ESPN already found it manually; CLS is obvious to an expert -> tool value
grows where the wasteful class is NON-OBVIOUS (still to be found). Also re-confirmed /proc/self/io
read_bytes DOES count GDS reads (300MiB) -> per-process attribution is NOT a gap (iotop sees it).

## Real workload: RAPIDS cuDF read_parquet -- HONEST NEGATIVE (2026-06-08)
cuDF 26.04 read_parquet (NYC taxi, 2.96M rows x 19 cols x 3 row-groups, 47.6MiB) via cuFile (GDS engages
even at LIBCUDF_CUFILE_POLICY=OFF; readMiB +48). workloads/cudf_read_parquet.py. GDS-Trace: 58
per-column-chunk cuFileReads, heterogeneous 17KB-4MB (match parquet column-chunk sizes; >4MB sliced to
4MiB). Per-op byte-amp: small cols 1.1-1.4x, large ~1.0x; AGGREGATE ~1.0x (48=48 MiB). device-cmd amp
2.05x (MDTS, bytes conserved). corr_id 94% (cuDF thread-pool -> some concurrent reads -> count>1 ambig).
CONCLUSION: cuDF Parquet I/O is WELL-ENGINEERED -- no significant byte-amp pathology; small-column reads
waste a little but negligible aggregate. Tool correctly reports a clean bill of health. Pathology shows
in naive/hand-rolled code (ESPN-naive), not well-tuned libs -> tool value = diagnosing naive/misconfigured
GDS. (Rigor: report negatives.) NEXT: elbencho deep-dive (richer benchmark, can stress small-files/dir-trees).
