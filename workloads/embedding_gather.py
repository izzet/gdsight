#!/usr/bin/env python
"""ESPN/DLRM-style embedding gather over GDS. Reads random fixed-size rows from a big table on NVMe
directly into GPU memory via cuFile (cufile-python). A row is one embedding vector; its byte size is
`--rowbytes` (e.g. 768-dim fp32 = 3072 B, which is NOT 4 KiB-aligned). Most row offsets then land
off a 4 KiB boundary, so the device must read the 4 KiB-aligned superset -> BYTE amplification: the
genuine wasted-bandwidth overhead in the small-random-read regime GDS is sold for (retrieval,
embeddings, ESPN). Pick `--rowbytes 4096` for the aligned contrast (no waste). Run under GDS-Trace.

Opens the table ONCE and reuses one GPU buffer (register-once; contrast with DALI/ESPN/LMCache per-op
handle churn). Usage: embedding_gather.py [--path FILE] [--rowbytes N] [--nreads N] [--seed N]
"""
import argparse, ctypes, os, random, time
import torch
import cufile


def reads_counter():
    try:
        for ln in open("/proc/driver/nvidia-fs/stats"):
            if ln.startswith("Reads") and "readMiB" in ln:
                return ln.strip()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--rowbytes", type=int, default=3072, help="embedding row size (3072=768xfp32, unaligned)")
    ap.add_argument("--nreads", type=int, default=4000)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    random.seed(args.seed)
    fsz = os.path.getsize(args.path)
    nrows = fsz // args.rowbytes
    offsets = [random.randrange(nrows) * args.rowbytes for _ in range(args.nreads)]

    driver = cufile.CuFileDriver()
    buf = torch.empty(args.rowbytes, dtype=torch.uint8, device=args.device)
    addr = ctypes.c_void_p(buf.data_ptr())

    b = reads_counter()
    torch.cuda.synchronize(); t0 = time.time()
    with cufile.CuFile(args.path, "r") as f:   # open table once
        for off in offsets:
            f.read(addr, args.rowbytes, file_offset=off, dev_offset=0)
    torch.cuda.synchronize(); dt = time.time() - t0
    a = reads_counter()

    req = args.nreads * args.rowbytes / 2**20
    aligned = (args.rowbytes % 4096 == 0)
    print(f"GATHER rowbytes={args.rowbytes} ({'4KiB-aligned' if aligned else 'UNALIGNED'}) "
          f"nreads={args.nreads} requested={req:.1f}MiB in {dt:.3f}s ({req/dt:.0f}MiB/s)")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")


if __name__ == "__main__":
    main()
