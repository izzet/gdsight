#!/usr/bin/env python3
"""Build the final tracer-overhead table (Table C, §5.x) from raw per-iter gdsio
throughputs. Random rows come from the 256M/N5 run (already tight); sequential rows
from the 2G/N7 de-noise run. Adds an achieved-IOPS column (from baseline BW & I/O size)
to make the mechanism — overhead tracks ops/sec, not pattern — visible directly.

Usage: overhead_table.py <rand_raw> <seq_raw> > overhead_final.txt
Raw format (per cell, two lines):
  "<size> <pat> baseline: v1 v2 ..."
  "<size> <pat> traced:   v1 v2 ..."
"""
import sys, re, math

SIZE_BYTES = {"4K":4096,"16K":16384,"64K":65536,"256K":262144,"1M":1048576,"4M":4194304}
ORDER = ["4K","16K","64K","256K","1M","4M"]

def parse(path, want_pat):
    cells = {}
    for ln in open(path):
        m = re.match(r"(\S+)\s+(\w+)\s+(baseline|traced):\s+(.*)", ln)
        if not m: continue
        sz, pat, kind, vals = m.groups()
        if pat != want_pat: continue
        cells.setdefault((sz,pat),{})[kind] = [float(x) for x in vals.split()]
    return cells

def stats(v):
    m = sum(v)/len(v)
    sd = math.sqrt(sum((x-m)**2 for x in v)/(len(v)-1)) if len(v)>1 else 0.0
    return m, sd

def main():
    rand_raw, seq_raw = sys.argv[1], sys.argv[2]
    cells = {}
    cells.update(parse(seq_raw, "seq"))
    cells.update(parse(rand_raw, "rand"))
    print("size  | pat  | base GiB/s ± sd     | traced GiB/s ± sd   | overhead | achieved kIOPS (base) | N")
    print("------+------+---------------------+---------------------+----------+-----------------------+---")
    for sz in ORDER:
        for pat in ("seq","rand"):
            c = cells.get((sz,pat))
            if not c: continue
            bm,bs = stats(c["baseline"]); tm,ts = stats(c["traced"])
            ov = (bm-tm)/bm*100 if bm>0 else 0.0
            kiops = bm*(2**30)/SIZE_BYTES[sz]/1000.0   # baseline achieved kIOPS
            n = len(c["baseline"])
            print(f"{sz:5} | {pat:4} | {bm:7.3f} ± {bs:5.3f}     | {tm:7.3f} ± {ts:5.3f}     | {ov:6.1f}%  | {kiops:9.1f}             | {n}")

if __name__ == "__main__":
    main()
