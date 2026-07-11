#!/usr/bin/env python
"""Multi-file GDS reader: kvikio reads aligned 64 KiB blocks (>= threshold -> true GDS) from TWO files
in one process, so the tracer sees NVMe commands for both. Used to confirm multi-file address
attribution (tools/xfile_addr_attr.py): each command's LBA must map back to the correct file."""
import argparse, cupy, kvikio

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fa", required=True)
    ap.add_argument("--fb", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--stride", type=int, default=2 << 20)  # 2 MiB apart
    ap.add_argument("--sz", type=int, default=65536)        # 64 KiB, above the 16 KiB GDS threshold
    a = ap.parse_args()
    buf = cupy.empty(a.sz, dtype=cupy.uint8)
    with kvikio.CuFile(a.fa, "r") as fa:
        for i in range(a.n):
            fa.read(buf, a.sz, i * a.stride)
    with kvikio.CuFile(a.fb, "r") as fb:
        for i in range(a.n):
            fb.read(buf, a.sz, i * a.stride)
    print(f"XFILE_READ n={a.n} sz={a.sz} from A and B")

if __name__ == "__main__":
    main()
