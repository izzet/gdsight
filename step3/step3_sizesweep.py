#!/usr/bin/env python
"""step3_sizesweep.py — read `count` fixed-size, 4K-aligned reads via kvikio.CuFile into GPU memory.
Used to find the read size at which kvikio stops using GDS and silently falls to POSIX (measured
externally via the kernel nvidia-fs Reads/readMiB counters). Aligned offsets + (small) fixed sizes
isolate the *size* variable. Reads-only."""
import os, time, argparse, numpy as np, cupy as cp, kvikio
ap = argparse.ArgumentParser()
ap.add_argument("--file", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
ap.add_argument("--size", type=int, required=True)   # bytes per read
ap.add_argument("--count", type=int, required=True)
ap.add_argument("--seed", type=int, default=0)
args = ap.parse_args()
flen = os.path.getsize(args.file); A = 4096; sz = args.size
rng = np.random.default_rng(args.seed)
offs = (rng.integers(0, (flen - sz) // A, size=args.count) * A).tolist()
buf = cp.empty(sz, dtype=cp.uint8)
f = kvikio.CuFile(args.file, "r")
cp.cuda.runtime.deviceSynchronize()
total = 0; t0 = time.perf_counter()
for off in offs:
    total += f.read(buf, sz, file_offset=off)
cp.cuda.runtime.deviceSynchronize()
dt = time.perf_counter() - t0
f.close()
print(f"size={sz} count={args.count} total_MiB={total/2**20:.1f} time={dt:.2f}s bw={total/2**30/dt:.3f}GiB/s")
