#!/usr/bin/env python
"""step3_npy.py — Step 3 realism: multi-threaded kvikio reads over a REAL .npy dataset.

Real .npy files store the array data right after a header (\x93NUMPY + version + header), so the data
region starts at an offset like 128 B — NOT 4K-aligned. A vendor GDS reader therefore hits the
unaligned path *naturally* on real files (no synthetic offsets), exposing the silent GDS bypass.

  --make N : create N .npy files of varied (ragged) sizes, then exit
  default  : read every .npy's data region into GPU memory via kvikio, multi-threaded; loop for --secs
"""
import os, sys, time, glob, argparse, threading, numpy as np, cupy as cp, kvikio
from concurrent.futures import ThreadPoolExecutor
import kvikio.defaults as kd

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default="/mnt/nvme1/gdstrace-smoke/npy")
ap.add_argument("--make", type=int, default=0)
ap.add_argument("--threads", type=int, default=8)
ap.add_argument("--secs", type=float, default=0)
args = ap.parse_args()

if args.make:
    os.makedirs(args.dir, exist_ok=True)
    rng = np.random.default_rng(0)
    for i in range(args.make):
        n = int(rng.integers(16 * 1024, 512 * 1024))   # varied element counts -> ragged file sizes
        np.save(os.path.join(args.dir, f"a{i:05d}.npy"), np.arange(n, dtype=np.float32))
    print(f"created {args.make} .npy files in {args.dir}")
    sys.exit(0)

files = sorted(glob.glob(os.path.join(args.dir, "*.npy")))
assert files, f"no .npy in {args.dir}; run --make first"

def region(path):                       # (data_offset, nbytes) for an npy v1.0 file
    with open(path, "rb") as fp:
        fp.read(8)                      # magic(6) + version(2)
        hlen = int.from_bytes(fp.read(2), "little")
        off = 10 + hlen
    return off, os.path.getsize(path) - off

reg = {f: region(f) for f in files}
MAX = max(nb for _, nb in reg.values())
tls = threading.local()
def buf():
    b = getattr(tls, "b", None)
    if b is None:
        b = cp.empty(MAX, dtype=cp.uint8); tls.b = b
    return b

def read_one(path):
    off, nb = reg[path]
    f = kvikio.CuFile(path, "r")
    got = f.read(buf(), nb, file_offset=off)
    f.close()
    return got

avg_off = sum(o for o, _ in reg.values()) // len(reg)
print(f"kvikio {kvikio.__version__} compat_pref={kd.is_compat_mode_preferred()} files={len(files)} "
      f"threads={args.threads} avg_npy_data_offset={avg_off}B (4K-aligned={avg_off % 4096 == 0})")

total = nrec = 0
t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=args.threads) as ex:
    while True:
        for got in ex.map(read_one, files):
            total += got; nrec += 1
        if args.secs <= 0 or time.perf_counter() - t0 >= args.secs:
            break
dt = time.perf_counter() - t0
print(f"reads={nrec} total={total/2**30:.2f} GiB time={dt:.2f}s throughput={total/2**30/dt:.3f} GiB/s")
