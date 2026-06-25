#!/usr/bin/env python
"""RAG/inference-style heterogeneous GDS load via the REAL kvikio library (not a C probe).
Two op-classes a real pipeline mixes, read directly to GPU with kvikio:
  A = large model-shard reads (1 MiB, 4K-aligned)         -> clean
  B = scattered small embedding/KV rows (3 KiB, unaligned) -> silent read-amplification
T worker threads issue them concurrently (a data loader). Class is encoded by file region
(A < 1 GiB, B >= 1 GiB of the backing file) so the tracer's LBA attribution can split them.
Trace under DATACRUMBS trace-all (no injection). Usage: rag_mixed.py [--threads 8]"""
import argparse, threading, time
import cupy, kvikio

ABASE = 0
BBASE = 1 << 30            # class B region starts at 1 GiB

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/ovh.dat")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--na", type=int, default=200)   # large shard reads
    ap.add_argument("--sa", type=int, default=1<<20)
    ap.add_argument("--nb", type=int, default=2000)  # small embedding/KV reads
    ap.add_argument("--sb", type=int, default=3072)
    a = ap.parse_args()
    # build the interleaved op list: (offset, size)
    ops = []
    for i in range(a.nb):
        ops.append((BBASE + i*65536 + 3072, a.sb))   # unaligned, scattered, spans 2 blocks
        if i % (max(1, a.nb//a.na)) == 0 and len(ops) < a.na+a.nb:
            j = i // max(1, a.nb//a.na)
            if j < a.na: ops.append((ABASE + j*(4<<20), a.sa))
    # split ops across threads
    chunks = [ops[t::a.threads] for t in range(a.threads)]
    bufA = [cupy.empty(a.sa, dtype=cupy.uint8) for _ in range(a.threads)]
    bufB = [cupy.empty(a.sb, dtype=cupy.uint8) for _ in range(a.threads)]
    def work(t, f):
        for off, sz in chunks[t]:
            buf = bufA[t] if sz == a.sa else bufB[t]
            f.read(buf, sz, off)
    t0 = time.time()
    with kvikio.CuFile(a.path, "r") as f:
        ths = [threading.Thread(target=work, args=(t, f)) for t in range(a.threads)]
        for th in ths: th.start()
        for th in ths: th.join()
    dt = time.time() - t0
    print(f"RAG_MIXED na={a.na} sa={a.sa} nb={a.nb} sb={a.sb} threads={a.threads} ops={len(ops)} in {dt:.3f}s")

if __name__ == "__main__":
    main()
