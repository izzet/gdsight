#!/usr/bin/env python
"""Verify the cuCIM/WSI 'coalesce' lever: for a contiguous run of tiles (a region scan), does reading the
tiles' contiguous byte-span as large GDS reads (>=chunk) beat reading each small tile individually (which
falls below kvikio's 16 KiB GDS threshold -> POSIX)? Same useful data, two I/O strategies. (Coalescing only
applies to sequential access; random patch gather cannot coalesce.) Usage: --ntiles 4000 --chunk 1048576"""
import argparse, math, os, time
import numpy as np
import tifffile
import cupy
import kvikio


def readMiB():
    for ln in open("/proc/driver/nvidia-fs/stats"):
        if ln.startswith("Reads") and "readMiB" in ln:
            return int(ln.split("readMiB=")[1].split()[0])
    return 0


def drop_caches():
    os.system("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svs", default="/mnt/nvme1/gdstrace-smoke/cucim/CMU-1.svs")
    ap.add_argument("--ntiles", type=int, default=4000)
    ap.add_argument("--chunk", type=int, default=1024 * 1024)
    args = ap.parse_args()

    p = tifffile.TiffFile(args.svs).pages[0]
    off = np.asarray(p.dataoffsets); bc = np.asarray(p.databytecounts)
    order = np.argsort(off)[: args.ntiles]            # first N tiles in FILE ORDER (a contiguous region)
    off, bc = off[order], bc[order]
    span_lo, span_hi = int(off[0]), int(off[-1] + bc[-1])
    span = span_hi - span_lo
    useful = int(bc.sum())
    print(f"region: {args.ntiles} contiguous tiles | useful {useful/2**20:.1f} MiB | "
          f"file-span {span/2**20:.1f} MiB (gap overhead {100*(span-useful)/useful:.1f}%)")

    # --- baseline: per-tile small reads (kvikio default -> mostly POSIX) ---
    drop_caches()
    bufs = [cupy.empty(int(b), dtype=cupy.uint8) for b in bc]
    g0 = readMiB(); t0 = time.time()
    with kvikio.CuFile(args.svs, "r") as f:
        for j in range(len(off)):
            fut = f.read(bufs[j], int(bc[j]), file_offset=int(off[j]))
            if hasattr(fut, "get"):
                fut.get()
    cupy.cuda.runtime.deviceSynchronize()
    t_base = time.time() - t0; gds_base = readMiB() - g0

    # --- coalesced: contiguous span as large GDS reads ---
    drop_caches()
    cbuf = cupy.empty(args.chunk, dtype=cupy.uint8)
    g1 = readMiB(); t1 = time.time()
    with kvikio.CuFile(args.svs, "r") as f:
        pos = span_lo
        while pos < span_hi:
            n = min(args.chunk, span_hi - pos)
            fut = f.read(cbuf[:n], n, file_offset=pos)
            if hasattr(fut, "get"):
                fut.get()
            pos += n
    cupy.cuda.runtime.deviceSynchronize()
    t_coal = time.time() - t1; gds_coal = readMiB() - g1

    print(f"  BASELINE per-tile ({len(off)} reads, {int(bc.mean())} B avg): {t_base:.3f}s | GDS readMiB +{gds_base} (mostly POSIX)")
    print(f"  COALESCED {args.chunk//1024}KiB ({math.ceil(span/args.chunk)} reads): {t_coal:.3f}s | GDS readMiB +{gds_coal} (true GDS)")
    print(f"  => coalescing speedup: {t_base/t_coal:.2f}x")


if __name__ == "__main__":
    main()
