#!/usr/bin/env python
"""Concurrency stress for cross-layer attribution: T threads issue cuFileReads at random offsets
concurrently, so many ops are in flight at once and cuFile's internal threading decouples the
read-submitting thread from the thread/time the device command fires. This is the regime where the
timing/thread corr_id heuristic degrades (per-tgid op-count > 1, per-tid current-op overwritten) — the
case the deterministic LBA matcher is meant to fix. Usage: --threads 8 --nreads 4000"""
import argparse, ctypes, os, random, threading, time
import torch
import cufile


def worker(path, size, nreads, fsz, seed, dev):
    rnd = random.Random(seed)
    buf = torch.empty(size, dtype=torch.uint8, device=dev)
    addr = ctypes.c_void_p(buf.data_ptr())
    nslots = fsz // size
    with cufile.CuFile(path, "r") as f:
        for _ in range(nreads):
            f.read(addr, size, file_offset=rnd.randrange(nslots) * size, dev_offset=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--nreads", type=int, default=4000, help="total reads across all threads")
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--device", default="cuda:0")
    args = ap.parse_args()

    fsz = os.path.getsize(args.path)
    per = args.nreads // args.threads
    cufile.CuFileDriver()
    torch.empty(1, device=args.device)  # init CUDA context before threads
    ts = [threading.Thread(target=worker, args=(args.path, args.size, per, fsz, 100 + i, args.device))
          for i in range(args.threads)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dt = time.time() - t0
    tot = per * args.threads
    print(f"CONCURRENT: {args.threads} threads x {per} reads ({tot} total, {args.size}B) in {dt:.3f}s "
          f"({tot*args.size/2**20/dt:.0f} MiB/s)")


if __name__ == "__main__":
    main()
