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
- **(2b) batch:** `cuFileBatchIOSubmit` captured (gdsio -x6 → 125 events) but **duration-only** — the
  `CUfileIOParams_t` array-walk for per-op size is the remaining piece (ESPN uses batch).
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
