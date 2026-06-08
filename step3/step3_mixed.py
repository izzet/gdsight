#!/usr/bin/env python
"""step3_mixed.py — realistic *bimodal* kvikio read workload: a mix of small (<16KiB, sub-GDS-
threshold) and large (>=64KiB) reads, like a metadata/embedding-heavy or variable-chunk dataset.
Small reads silently take kvikio's POSIX path (no GDS DMA, invisible to gds_stats); large reads use
GDS. Reports the small/large byte split so it can be compared against the kernel nvidia-fs DMA counter
and gds_stats. Reads-only."""
import os, time, argparse, numpy as np, cupy as cp, kvikio
ap = argparse.ArgumentParser()
ap.add_argument("--file", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
ap.add_argument("--p-small", type=float, default=0.6, help="fraction of *reads* that are small (<16KiB)")
ap.add_argument("--secs", type=float, default=15.0)
ap.add_argument("--seed", type=int, default=0)
args = ap.parse_args()
flen = os.path.getsize(args.file); A = 4096; MAX = 512 * 1024
rng = np.random.default_rng(args.seed)
buf = cp.empty(MAX, dtype=cp.uint8)
f = kvikio.CuFile(args.file, "r")
small_b = large_b = small_n = large_n = 0
t0 = time.perf_counter()
while time.perf_counter() - t0 < args.secs:
    for _ in range(2000):
        if rng.random() < args.p_small:
            sz = int(rng.integers(1, 4)) * A          # 4–12 KiB  (sub-threshold -> POSIX)
            small_n += 1; small_b += sz
        else:
            sz = int(rng.integers(16, 129)) * A       # 64 KiB–512 KiB (>=16KiB -> GDS)
            large_n += 1; large_b += sz
        off = int(rng.integers(0, (flen - sz) // A)) * A
        f.read(buf, sz, file_offset=off)
    if time.perf_counter() - t0 >= args.secs:
        break
f.close()
tot = small_b + large_b
print(f"reads: small(<16K)={small_n} ({small_b/2**20:.0f} MiB)  large(>=64K)={large_n} ({large_b/2**20:.0f} MiB)")
print(f"total={tot/2**30:.2f} GiB | EXPECTED GDS-DMA bytes = large only = {large_b/2**20:.0f} MiB "
      f"({100*large_b/tot:.0f}% of bytes); small {100*small_b/tot:.0f}% of bytes silently on POSIX")
