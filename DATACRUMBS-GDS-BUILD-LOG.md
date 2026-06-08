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
- [ ] add **block/bio kprobe** layer → cuFileRead↔NVMe amplification correlation
- [ ] DFAnalyzer (`feat/datacrumbs`) → per-op cuFile↔bio view

## ✅ (1) per-op cuFile arg capture (size/offset)
`gdsio -i 1M` → 512 `cuFileRead` events, all with `args:{"size":1048576,"offset":<varies>}`. Now each
GDS op is attributed by **size + file_offset + duration + thread** — the per-op data needed for
amplification/path analysis (e.g., which ops are sub-threshold, which span pages).

**How:** since our only uprobe category is `cufile`, extended the *generic* uprobe path (no separate
plugin needed): capture `PT_REGS_PARM3`=size, `PT_REGS_PARM4`=file_offset at uprobe **entry** into
`fn_value_t`, carry to `general_event_t` at **exit**, emit as args in `general_event.h` get_data_1.
Added `size`/`offset` to `general_event_t`+`fn_value_t` (`shared.h`), zeroed in `init.bpf.c`.
Matches the cuFile **sync** ABI: `cuFileRead(fh, buf, size, file_offset, buf_offset)`.

**Caveat / next:** correct for **sync `cuFileRead`/`cuFileWrite`** (PARM3=size). For
`cuFileReadAsync` (size is a `size_t*`) and `cuFileBatchIOSubmit` (array of params), PARM3 is a
pointer/count, not the size → needs deref/array handling (future; kvikio/ESPN use these).

**Gotcha:** after changing a shared BPF header (`shared.h`/`common.h`), the BPF link
(`bpftool gen object`) fails with `Invalid argument` on a stale object with mismatched BTF — do a
**clean BPF rebuild** (`make clean_all` + rm `libexec/.../objects/*.o`).

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
