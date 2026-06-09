#!/usr/bin/env python
"""Isolate the cuCIM read_region(device='cuda') hang: vary device (cpu/cuda), num_workers, npatches under a
timeout to find what blocks. Usage: --device cuda --num-workers 1 --npatches 4"""
import argparse, time, sys
import numpy as np
from cucim import CuImage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svs", default="/mnt/nvme1/gdstrace-smoke/cucim/CMU-1.svs")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--num-workers", type=int, default=1)
    ap.add_argument("--npatches", type=int, default=4)
    ap.add_argument("--batch", type=int, default=1)
    args = ap.parse_args()

    print(f"opening {args.svs} ...", flush=True)
    img = CuImage(args.svs)
    k = max(1, int(args.npatches ** 0.5))
    locs = [[x * 512, y * 512] for x in range(k) for y in range(k)][: args.npatches]
    print(f"read_region: device={args.device} num_workers={args.num_workers} npatches={len(locs)} "
          f"batch={args.batch} ...", flush=True)
    t0 = time.time()
    region = img.read_region(locs, (224, 224), batch_size=args.batch,
                             num_workers=args.num_workers, device=args.device)
    n = 0
    for batch in region:          # count batches (avoid host/GPU conversion); completing = no hang
        n += 1
    print(f"DONE device={args.device} nw={args.num_workers}: {n} batches in {time.time()-t0:.2f}s", flush=True)


if __name__ == "__main__":
    main()
