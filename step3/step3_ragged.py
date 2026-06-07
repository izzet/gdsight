#!/usr/bin/env python
"""step3_ragged.py — Step 3 of the GDS-Trace Appendix-A plan.

Drive kvikio (cuFile/GDS) over a dataset of records (offset,size) read into GPU memory, in three
modes:
  aligned : 4K-aligned offsets + 4K-multiple sizes        (GDS-friendly)
  ragged  : arbitrary offsets + arbitrary sizes           (like variable-size *compressed* chunks)
  mixed   : interleave aligned + ragged                   (realistic: a SUBSET is ragged)

Prints achieved throughput. Pair with CUFILE stats (cufile_stats:3) to read the per-run
posix= / unalign= counters from the cufile log -> reveals whether a subset silently fell back to
POSIX or amplified, while the aggregate throughput looks "fine".

Reads-only (safe). The dataset file must already exist on the GDS mount (create with dd).
"""
import os, time, argparse, numpy as np, cupy as cp, kvikio
import kvikio.defaults as kd

ap = argparse.ArgumentParser()
ap.add_argument("--file", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
ap.add_argument("--mode", choices=["aligned", "ragged", "mixed"], default="mixed")
ap.add_argument("--nreads", type=int, default=2000)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--secs", type=float, default=0,
                help="if >0, loop the record set for this many seconds (keeps the process alive "
                     "long enough for a live gds_stats attach; cuFile crashes at exit on this stack)")
args = ap.parse_args()

flen = os.path.getsize(args.file)
A = 4096
MAX = 1 << 20                      # 1 MiB max record
rng = np.random.default_rng(args.seed)

def gen(mode, n):
    out = []
    for i in range(n):
        ragged = (mode == "ragged") or (mode == "mixed" and i % 2 == 1)
        if ragged:
            sz = int(rng.integers(50 * 1024, MAX))            # arbitrary size (not a 4K multiple)
            off = int(rng.integers(0, flen - sz))             # arbitrary (unaligned) offset
        else:
            sz = int(rng.integers(16, MAX // A + 1)) * A      # 4K-multiple size, up to 1 MiB
            off = int(rng.integers(0, (flen - sz) // A)) * A  # 4K-aligned offset
        out.append((off, sz))
    return out

records = gen(args.mode, args.nreads)
buf = cp.empty(MAX, dtype=cp.uint8)
print(f"kvikio {kvikio.__version__} | compat_preferred={kd.is_compat_mode_preferred()} | "
      f"mode={args.mode} nreads={args.nreads}")

total = 0
nrec = 0
f = kvikio.CuFile(args.file, "r")
cp.cuda.runtime.deviceSynchronize()
t0 = time.perf_counter()
while True:
    for off, sz in records:
        total += f.read(buf, sz, file_offset=off)  # read sz bytes at file offset off into GPU buf
        nrec += 1
    if args.secs <= 0 or (time.perf_counter() - t0) >= args.secs:
        break
cp.cuda.runtime.deviceSynchronize()
dt = time.perf_counter() - t0
f.close()
print(f"records={nrec} total={total/2**30:.2f} GiB time={dt:.3f}s "
      f"throughput={total/2**30/dt:.3f} GiB/s  avg_rec={total/nrec/1024:.1f} KiB")
