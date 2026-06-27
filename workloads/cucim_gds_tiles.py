#!/usr/bin/env python
"""Real scientific workload: whole-slide image (WSI) tiled reads via GDS, the flagship cuCIM/digital-
pathology GDS use case. Reads level-0 JPEG tiles of an Aperio SVS directly to GPU with kvikio (the path
cuCIM's gds_whole_slide benchmark uses). WSI tiles are small (~2-34 KB) and strided, so they sit *below*
kvikio's default 16 KiB GDS threshold -> they silently use POSIX, not GDS, unless KVIKIO_GDS_THRESHOLD=0.
Run under GDSight to see, per-tile, which reads actually hit GDS vs silently fall to POSIX, and the
byte-amplification of the small/unaligned tiles. Usage: --ntiles 4000 [env KVIKIO_GDS_THRESHOLD=0 to force]"""
import argparse, os, time
import numpy as np
import tifffile
import cupy
import kvikio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svs", default="/mnt/nvme1/gdstrace-smoke/cucim/CMU-1.svs")
    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--ntiles", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    t = tifffile.TiffFile(args.svs)
    p = t.pages[args.level]
    off = np.asarray(p.dataoffsets)
    bc = np.asarray(p.databytecounts)
    n = min(args.ntiles, len(off))
    rng = np.random.default_rng(args.seed)
    idx = rng.choice(len(off), size=n, replace=False)  # random tiles (realistic patch access)

    def reads_counter():
        for ln in open("/proc/driver/nvidia-fs/stats"):
            if ln.startswith("Reads") and "readMiB" in ln:
                return int(ln.split("readMiB=")[1].split()[0])
        return 0

    bufs = [cupy.empty(int(bc[i]), dtype=cupy.uint8) for i in idx]
    b = reads_counter()
    t0 = time.time()
    with kvikio.CuFile(args.svs, "r") as f:
        for j, i in enumerate(idx):
            fut = f.read(bufs[j], int(bc[i]), file_offset=int(off[i]))
            if hasattr(fut, "get"):
                fut.get()
    cupy.cuda.runtime.deviceSynchronize()
    dt = time.time() - t0
    a = reads_counter()

    tot = int(bc[idx].sum())
    thr = os.environ.get("KVIKIO_GDS_THRESHOLD", "<default 16KiB>")
    print(f"CUCIM-TILES: {n} WSI tiles ({tot/2**20:.1f} MiB, mean {tot//n} B/tile) in {dt:.3f}s | "
          f"KVIKIO_GDS_THRESHOLD={thr}")
    print(f"  nvidia-fs readMiB delta = {a-b} MiB  (≈{tot/2**20:.0f} if all GDS; ≈0 if tiles silently POSIX)")


if __name__ == "__main__":
    main()
