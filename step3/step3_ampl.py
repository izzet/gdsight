#!/usr/bin/env python
"""step3_ampl.py — measure GDS I/O *amplification*: issue `count` cuFile reads of `size` into a GPU
buffer, with controllable FILE-offset misalignment and GPU-BUFFER misalignment, so the kernel
nvidia-fs Reads counter (measured externally) reveals how many NVMe/P2P-DMA requests one logical op
becomes. Reproduces the 64 KiB GPU-page effect (Muradli et al.). Reads-only (safe on this node)."""
import os, time, argparse, numpy as np, cupy as cp, kvikio
ap = argparse.ArgumentParser()
ap.add_argument("--file", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
ap.add_argument("--size", type=int, required=True)            # bytes per read
ap.add_argument("--count", type=int, required=True)
ap.add_argument("--file-misalign", type=int, default=0)       # bytes added to a 4K-aligned file offset
ap.add_argument("--buf-misalign", type=int, default=0)        # GPU buffer byte offset (vs its 64K page)
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
flen = os.path.getsize(a.file); A = 4096; sz = a.size; fm = a.file_misalign; bm = a.buf_misalign
rng = np.random.default_rng(a.seed)
buf = cp.empty(bm + sz, dtype=cp.uint8); view = buf[bm:bm + sz]
maxbase = (flen - sz - fm) // A
offs = (rng.integers(0, maxbase, size=a.count) * A + fm).tolist()
f = kvikio.CuFile(a.file, "r"); cp.cuda.runtime.deviceSynchronize()
total = errs = 0; t0 = time.perf_counter()
for off in offs:
    try: total += f.read(view, sz, file_offset=off)
    except Exception: errs += 1
cp.cuda.runtime.deviceSynchronize(); dt = time.perf_counter() - t0; f.close()
print(f"size={sz} count={a.count} file_misalign={fm} buf_misalign={bm} "
      f"req_MiB={a.count*sz/2**20:.1f} got_MiB={total/2**20:.1f} errs={errs} time={dt:.2f}s")
