#!/usr/bin/env python
"""Async concurrency: many cuFileReadAsync (kvikio raw_read_async) in flight on a CUDA stream. Unlike the
synchronous/threaded case, the I/O is driven by cuFile internally (stream-ordered, no caller thread), so
the device command fires decoupled from the submitting thread and from any single op's time window — the
regime where the timing/thread corr_id heuristic genuinely degrades, and where deterministic LBA matching
should still attribute every command. Usage: --nreads 4000 --inflight 64"""
import argparse, os, random
import cupy
import kvikio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--nreads", type=int, default=4000)
    ap.add_argument("--inflight", type=int, default=64, help="ring of buffers / async ops in flight")
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    random.seed(args.seed)
    fsz = os.path.getsize(args.path)
    nslots = fsz // args.size
    stream = cupy.cuda.Stream(non_blocking=True)
    bufs = [cupy.empty(args.size, dtype=cupy.uint8) for _ in range(args.inflight)]

    import time
    t0 = time.time()
    done = 0
    with kvikio.CuFile(args.path, "r") as f:
        while done < args.nreads:           # waves of `inflight` async ops; buffers reused only after sync
            n = min(args.inflight, args.nreads - done)
            futs = [f.raw_read_async(bufs[j], stream.ptr, args.size, random.randrange(nslots) * args.size, 0)
                    for j in range(n)]
            stream.synchronize()
            done += n
    dt = time.time() - t0
    print(f"ASYNC: {args.nreads} cuFileReadAsync ({args.size}B, {args.inflight} in flight) in {dt:.3f}s "
          f"({args.nreads*args.size/2**20/dt:.0f} MiB/s)")


if __name__ == "__main__":
    main()
