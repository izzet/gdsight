#!/usr/bin/env python
"""Curated anti-pattern #2 — the "GDS is healthy" threshold trap (real, documented). kvikio routes reads
below KVIKIO_GDS_THRESHOLD (default 16 KiB) through its own POSIX path *above* cuFile, silently — so a
workload with a mix of small and large reads has the large reads on true GDS and the small reads quietly
on POSIX. gds_stats / nvidia-fs show the large (GDS) reads ("GDS is working"); only per-op attribution
shows that the small reads bypass GDS entirely. Realistic: bimodal access (e.g. metadata + payload,
small features + big tensors). Usage: --frac-small 0.6"""
import argparse, os, random, time
import cupy
import kvikio

SMALL = 4096       # < 16 KiB  -> kvikio POSIX (silent)
LARGE = 65536      # >= 16 KiB -> true GDS via cuFile


def reads_counter():
    for ln in open("/proc/driver/nvidia-fs/stats"):
        if ln.startswith("Reads") and "readMiB" in ln:
            return ln.strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--nreads", type=int, default=4000)
    ap.add_argument("--frac-small", type=float, default=0.6, help="fraction of sub-16KiB (silent POSIX) reads")
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()

    random.seed(args.seed)
    fsz = os.path.getsize(args.path)
    sbuf = cupy.empty(SMALL, dtype=cupy.uint8)
    lbuf = cupy.empty(LARGE, dtype=cupy.uint8)
    plan = [("small", SMALL) if random.random() < args.frac_small else ("large", LARGE)
            for _ in range(args.nreads)]

    b = reads_counter()
    t0 = time.time()
    with kvikio.CuFile(args.path, "r") as f:
        for kind, sz in plan:
            off = random.randrange(fsz // sz) * sz
            fut = f.read(sbuf if kind == "small" else lbuf, file_offset=off)
            if hasattr(fut, "get"):
                fut.get()
    cupy.cuda.runtime.deviceSynchronize()
    dt = time.time() - t0
    a = reads_counter()

    ns = sum(1 for k, _ in plan if k == "small"); nl = args.nreads - ns
    print(f"KVIKIO-THRESHOLD: {args.nreads} reads ({ns} small<16KiB + {nl} large>=16KiB) in {dt:.3f}s")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")
    print(f"  (only the {nl} large reads should hit GDS/nvidia-fs; the {ns} small reads are silent POSIX)")


if __name__ == "__main__":
    main()
