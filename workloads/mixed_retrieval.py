#!/usr/bin/env python
"""Mixed retrieval/RAG-style embedding gather over GDS from TWO tables with different dims (a realistic
production mix): table A = 1024-dim fp32 (4096 B rows, 4 KiB-aligned, no waste) and table B = 768-dim
fp32 (3072 B rows, NOT 4 KiB-aligned -> 2x byte amplification). Reads are interleaved, so the AGGREGATE
looks like one healthy GDS stream. The question: can per-op below-cuFile attribution pinpoint that table
B silently wastes half its NVMe bandwidth, when Nsight (uniform cuFile latency) and gds_stats (aggregate)
cannot? The two tables are distinguishable in a trace by cuFileRead size (4096 vs 3072).

Run under: nsys (NVTX), GDS-Trace, and alongside gds_stats/nvidia-fs/iostat. Usage:
  mixed_retrieval.py [--path FILE] [--nreads N] [--seed N]
"""
import argparse, ctypes, os, random, time
import torch
import cufile

TABLES = {"A_1024d_aligned": 4096, "B_768d_unaligned": 3072}


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
    ap.add_argument("--nreads", type=int, default=4000, help="total reads (split 50/50 across tables)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    random.seed(args.seed)
    fsz = os.path.getsize(args.path)
    bufs = {s: torch.empty(s, dtype=torch.uint8, device=args.device) for s in TABLES.values()}

    plan = []
    for i in range(args.nreads):
        name = "A_1024d_aligned" if (i % 2 == 0) else "B_768d_unaligned"
        s = TABLES[name]
        off = random.randrange(fsz // s) * s
        plan.append((s, off))
    random.shuffle(plan)  # interleave the two tables into one stream

    b = reads_counter()
    torch.cuda.synchronize(); t0 = time.time()
    with cufile.CuFile(args.path, "r") as f:
        for s, off in plan:
            f.read(ctypes.c_void_p(bufs[s].data_ptr()), s, file_offset=off, dev_offset=0)
    torch.cuda.synchronize(); dt = time.time() - t0
    a = reads_counter()

    reqA = sum(1 for s, _ in plan if s == 4096) * 4096 / 2**20
    reqB = sum(1 for s, _ in plan if s == 3072) * 3072 / 2**20
    print(f"MIXED gather: {args.nreads} reads in {dt:.3f}s")
    print(f"  table A (1024d, 4096B aligned)  : {sum(1 for s,_ in plan if s==4096)} reads, {reqA:.2f} MiB requested")
    print(f"  table B (768d, 3072B unaligned) : {sum(1 for s,_ in plan if s==3072)} reads, {reqB:.2f} MiB requested")
    print(f"  total requested = {reqA+reqB:.2f} MiB")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")


if __name__ == "__main__":
    main()
