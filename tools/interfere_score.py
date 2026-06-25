#!/usr/bin/env python3
"""interfere_score.py — per-op latency distribution by size-class from a trace, to measure
cross-op interference (head-of-line blocking). Uses cuFileRead 'dur' (synchronous => true
op latency). Reports small-class and large-class latency percentiles. Compare a small-only
run vs a small+large run to see the tail inflation, attributed per-op cross-layer.

usage: interfere_score.py --trace T [--small 3072] [--label L]
"""
import argparse, gzip, json

def pct(xs, p):
    if not xs: return 0
    xs=sorted(xs); k=int(round((p/100)*(len(xs)-1))); return xs[k]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trace",required=True); ap.add_argument("--small",type=int,default=3072)
    ap.add_argument("--label",default="")
    a=ap.parse_args()
    small=[]; large=[]
    with gzip.open(a.trace,"rt",errors="replace") as f:
        for ln in f:
            ln=ln.strip().rstrip(",")
            if not ln or ln[0]!="{": continue
            try: ev=json.loads(ln)
            except Exception: continue
            if ev.get("name")!="cuFileRead": continue
            sz=int(ev.get("args",{}).get("size",0)); dur=float(ev.get("dur",0))
            (small if sz==a.small else large).append(dur)
    def row(name,xs):
        if not xs: return f"  {name:6}: (none)"
        return (f"  {name:6}: n={len(xs):5d}  mean={sum(xs)/len(xs):8.1f}  p50={pct(xs,50):8.1f}  "
                f"p90={pct(xs,90):8.1f}  p99={pct(xs,99):9.1f}  max={max(xs):9.1f}  (dur units)")
    print(f"=== {a.label or a.trace} : per-op cuFileRead latency by class ===")
    print(row("small",small)); print(row("large",large))
    # machine-readable
    if small:
        print(f"CSV,{a.label},small,{len(small)},{sum(small)/len(small):.1f},{pct(small,50):.1f},{pct(small,99):.1f},{max(small):.1f}")
    if large:
        print(f"CSV,{a.label},large,{len(large)},{sum(large)/len(large):.1f},{pct(large,50):.1f},{pct(large,99):.1f},{max(large):.1f}")

if __name__=="__main__":
    main()
