#!/usr/bin/env python
"""ESPN-style multi-vector retrieval gather over GDS, grounded in ESPN (arXiv:2312.05417): per candidate
document, a small CLS vector (128-dim fp16 = 256 B) plus a BOW multi-vector blob (~2 KB; paper: 2-10 KB),
16-bit, 4 KiB I/O blocks, ~K docs per query.

  --mode naive   : CLS and BOW are separate reads -> 2 block reads/doc. The 256 B CLS read pulls a full
                   4 KiB block (16x byte-amp); BOW 2 KB -> 1 block. (the un-optimized layout)
  --mode aligned : ESPN's optimization -- CLS+BOW packed contiguous in one 4 KiB-aligned slot -> 1 read/doc.

Run under GDSight: per-read-CLASS attribution (by size: 256 B CLS vs 2 KB BOW vs 2304 B packed) flags
the CLS reads as the redundant/wasteful class to pack -- the fix ESPN's authors made by hand. Reads from
a real allocated file (dataset.bin) so the device actually fetches blocks. Usage:
  espn_retrieval.py --mode {naive,aligned} [--docs-per-query K] [--queries N]
"""
import argparse, ctypes, os, random, time
import torch
import cufile

CLS = 256       # 128-dim fp16
BOW = 2048      # ~2 KB multi-vector blob (small-doc regime where ESPN's packing applies)
SLOT = 4096     # 4 KiB block; packed CLS+BOW lives in one aligned slot


def reads_counter():
    for ln in open("/proc/driver/nvidia-fs/stats"):
        if ln.startswith("Reads") and "readMiB" in ln:
            return ln.strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--mode", choices=["naive", "aligned"], default="naive")
    ap.add_argument("--docs-per-query", type=int, default=1000)
    ap.add_argument("--queries", type=int, default=4)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    random.seed(args.seed)
    fsz = os.path.getsize(args.path)
    cls_buf = torch.empty(CLS, dtype=torch.uint8, device=args.device)
    bow_buf = torch.empty(BOW, dtype=torch.uint8, device=args.device)
    pk_buf = torch.empty(CLS + BOW, dtype=torch.uint8, device=args.device)
    cls_a, bow_a, pk_a = (ctypes.c_void_p(b.data_ptr()) for b in (cls_buf, bow_buf, pk_buf))

    drv = cufile.CuFileDriver()
    ndoc_cls = fsz // CLS
    ndoc_bow = fsz // BOW
    ndoc_slot = fsz // SLOT
    b = reads_counter()
    torch.cuda.synchronize(); t0 = time.time()
    with cufile.CuFile(args.path, "r") as f:
        for q in range(args.queries):
            if args.mode == "naive":
                for _ in range(args.docs_per_query):
                    f.read(cls_a, CLS, file_offset=random.randrange(ndoc_cls) * CLS)   # 256B -> 1 block (16x)
                    f.read(bow_a, BOW, file_offset=random.randrange(ndoc_bow) * BOW)   # 2KB  -> 1 block
            else:  # aligned: CLS+BOW packed in one 4KiB-aligned slot -> 1 read/doc
                for _ in range(args.docs_per_query):
                    f.read(pk_a, CLS + BOW, file_offset=random.randrange(ndoc_slot) * SLOT)
    torch.cuda.synchronize(); dt = time.time() - t0
    a = reads_counter()

    ndocs = args.queries * args.docs_per_query
    reads = ndocs * (2 if args.mode == "naive" else 1)
    useful = ndocs * (CLS + BOW) / 2**20
    print(f"ESPN {args.mode}: {args.queries} queries x {args.docs_per_query} docs = {ndocs} docs in {dt:.3f}s "
          f"({ndocs/dt:.0f} docs/s)")
    print(f"  cuFile reads issued: {reads} ({reads/ndocs:.0f}/doc) | useful={useful:.2f} MiB")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")


if __name__ == "__main__":
    main()
