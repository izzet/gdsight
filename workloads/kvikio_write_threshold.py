#!/usr/bin/env python
"""kvikio write path: does the sub-threshold POSIX shortcut apply to writes, and what does it cost?

kvikio routes I/O below KVIKIO_GDS_THRESHOLD (Python default 16 KiB) through its own POSIX path ABOVE
cuFile. On the read side that makes the bypass invisible to cuFile (verified: gds_stats posix=0). If
the same holds for writes, the write case is strictly worse than the read case: the bypassed writes are
small, so the device must also read-modify-write whole 4 KiB blocks. That compound cost -- a bypass no
API observer can see, plus device reads on a pure-write workload -- is the thing to measure.

Usage: kvikio_write_threshold.py [--nwrites N] [--frac-small F] [--size-small B] [--offset-skew B]
"""
import argparse
import os
import random
import time

import cupy
import kvikio

SMALL = 4096      # < 16 KiB -> kvikio POSIX shortcut (above cuFile)
LARGE = 65536     # >= 16 KiB -> true GDS via cuFile


def nvfs_line(prefix):
    for ln in open("/proc/driver/nvidia-fs/stats"):
        if ln.startswith(prefix):
            return ln.strip()
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/kvw.bin")
    ap.add_argument("--nwrites", type=int, default=20000)
    ap.add_argument("--frac-small", type=float, default=0.6)
    ap.add_argument("--size-small", type=int, default=SMALL)
    ap.add_argument("--offset-skew", type=int, default=0,
                    help="bytes added to each offset; 512 makes writes misaligned to the 4 KiB grid")
    ap.add_argument("--skew-small-only", action="store_true",
                    help="skew ONLY the sub-threshold writes. The large writes stay aligned, so every "
                         "op cuFile can see is clean and gds_stats reports a healthy GDS workload, "
                         "while the ops it never sees carry the misalignment and its device cost.")
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()

    random.seed(args.seed)
    if not os.path.exists(args.path):                       # create with dd-equivalent, never GDS
        with open(args.path, "wb") as f:
            f.write(b"\0" * (1 << 30))
        os.sync()
    fsz = os.path.getsize(args.path)

    sbuf = cupy.zeros(args.size_small, dtype=cupy.uint8)
    lbuf = cupy.zeros(LARGE, dtype=cupy.uint8)
    plan = [("small", args.size_small) if random.random() < args.frac_small else ("large", LARGE)
            for _ in range(args.nwrites)]

    before_w, before_r = nvfs_line("Writes"), nvfs_line("Reads")
    t0 = time.time()
    with kvikio.CuFile(args.path, "r+") as f:
        for kind, size in plan:
            off = random.randrange(0, max(fsz - size - args.offset_skew, 1))
            skew = args.offset_skew
            if args.skew_small_only and kind != "small":
                skew = 0
            off = (off // 4096) * 4096 + skew               # aligned, or skewed off the 4 KiB grid
            buf = sbuf if kind == "small" else lbuf
            f.write(buf, buf.nbytes, off)                   # kvikio picks GDS vs POSIX by size
    dt = time.time() - t0

    nsmall = sum(1 for k, _ in plan if k == "small")
    mib = sum(s for _, s in plan) / 1048576
    print(f"  wrote {len(plan)} ops ({nsmall} small / {len(plan)-nsmall} large), "
          f"{mib:.1f} MiB in {dt:.2f}s = {mib/1024/dt:.3f} GiB/s")
    print(f"  nvidia-fs Writes before: {before_w}")
    print(f"  nvidia-fs Writes after : {nvfs_line('Writes')}")
    print(f"  nvidia-fs Reads  before: {before_r}")
    print(f"  nvidia-fs Reads  after : {nvfs_line('Reads')}")
    print(f"  (only the {len(plan)-nsmall} large writes should reach GDS; "
          f"the {nsmall} small ones take kvikio's POSIX path above cuFile)")


if __name__ == "__main__":
    main()
