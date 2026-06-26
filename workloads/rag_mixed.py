#!/usr/bin/env python
"""RAG/inference-style heterogeneous GDS load via the REAL kvikio library (not a C probe).
Two op-classes a real pipeline mixes, read directly to GPU with kvikio:
  A = large model-shard reads (1 MiB, 4K-aligned)         -> clean
  B = scattered small embedding/KV rows (3 KiB, unaligned) -> silent read-amplification
T worker threads issue them concurrently (a data loader). Class is encoded by file region
(A < 1 GiB, B >= 1 GiB of the backing file) so the tracer's LBA attribution can split them.
Trace under DATACRUMBS trace-all (no injection). Usage: rag_mixed.py [--threads 8]"""
import argparse, os, threading, time
# Pin the GDS threshold so the bypass boundary is unambiguous in the artifact: small reads
# (< 16 KiB) take the POSIX backend, larger reads take GDS. This is the kvikio *Python*
# binding default (gds_threshold=16384), set explicitly here so the result does not depend
# on which binding's default is in effect.
os.environ.setdefault("KVIKIO_GDS_THRESHOLD", "16384")
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
    # UC-B fix knob: coalesce class-B reads into contiguous 4K-aligned chunks of this size (>=16 KiB
    # GDS threshold) instead of scattered sub-threshold sb reads. 0 = baseline (silent POSIX bypass).
    ap.add_argument("--coalesce", type=int, default=0)
    a = ap.parse_args()
    # build the interleaved op list: (offset, size)
    ops = []
    if a.coalesce > 0:
        total_b = a.nb * a.sb
        nchunks = (total_b + a.coalesce - 1) // a.coalesce
        a_ops = [(ABASE + j*(4<<20), a.sa) for j in range(a.na)]          # SAME class-A workload as baseline
        b_ops = [(BBASE + k*a.coalesce, a.coalesce) for k in range(nchunks)]  # contiguous, aligned, >=threshold->GDS
        ia = ib = 0; ratio = max(1, len(b_ops) // max(1, len(a_ops)))     # interleave B-per-A
        while ia < len(a_ops) or ib < len(b_ops):
            for _ in range(ratio):
                if ib < len(b_ops): ops.append(b_ops[ib]); ib += 1
            if ia < len(a_ops): ops.append(a_ops[ia]); ia += 1
    else:
        for i in range(a.nb):
            ops.append((BBASE + i*65536 + 3072, a.sb))   # unaligned, scattered, spans 2 blocks -> POSIX bypass
            if i % (max(1, a.nb//a.na)) == 0 and len(ops) < a.na+a.nb:
                j = i // max(1, a.nb//a.na)
                if j < a.na: ops.append((ABASE + j*(4<<20), a.sa))
    bsz = max(a.sb, a.coalesce)
    # split ops across threads
    chunks = [ops[t::a.threads] for t in range(a.threads)]
    bufA = [cupy.empty(a.sa, dtype=cupy.uint8) for _ in range(a.threads)]
    bufB = [cupy.empty(bsz, dtype=cupy.uint8) for _ in range(a.threads)]
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
