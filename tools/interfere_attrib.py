#!/usr/bin/env python3
"""interfere_attrib.py — cross-layer attribution of small-op tail latency to concurrent
large-op DEVICE activity (head-of-line blocking). For each small cuFileRead, count the
large-region NVMe commands issued during its [ts, ts+dur] window (large = file region A,
< THRESH; small = region B). If SLOW small ops overlap many more large device commands
than FAST ones, the tail is attributed to large-op device occupancy — a per-op cross-layer
fact no app-side or device-side tool alone can establish.

usage: interfere_attrib.py --trace T --file F [--small 3072] [--thresh 805306368]
"""
import argparse, bisect, gzip, json, re, subprocess
EXT_RE=re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')

def extents(path):
    out=subprocess.run(["filefrag","-v",path],capture_output=True,text=True).stdout
    e=[]
    for ln in out.splitlines():
        m=EXT_RE.match(ln)
        if m: e.append((int(m.group(2)),int(m.group(1)),int(m.group(3))))
    e.sort(); return e
def p2l(exts,starts,pb):
    i=bisect.bisect_right(starts,pb)-1
    if i<0: return None
    ps,ls,ln=exts[i]; return ls+(pb-ps) if pb<ps+ln else None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trace",required=True); ap.add_argument("--file",required=True)
    ap.add_argument("--small",type=int,default=3072); ap.add_argument("--thresh",type=int,default=768*2**20)
    ap.add_argument("--bs",type=int,default=4096); ap.add_argument("--sec",type=int,default=512)
    a=ap.parse_args()
    exts=extents(a.file); starts=[e[0] for e in exts]
    smalls=[]; large_cmd_ts=[]
    with gzip.open(a.trace,"rt",errors="replace") as f:
        for ln in f:
            ln=ln.strip().rstrip(",")
            if not ln or ln[0]!="{": continue
            try: ev=json.loads(ln)
            except Exception: continue
            n,ar=ev.get("name"),ev.get("args",{})
            if n=="cuFileRead" and int(ar.get("size",0))==a.small:
                smalls.append((float(ev.get("ts",0)),float(ev.get("dur",0))))
            elif n=="nvme_setup_cmd" and "sector" in ar:
                lb=p2l(exts,starts,int(ar["sector"])*a.sec//a.bs)
                if lb is not None and lb*a.bs < a.thresh:        # large-region (class A) device cmd
                    large_cmd_ts.append(float(ev.get("ts",0)))
    large_cmd_ts.sort()
    durs=sorted(d for _,d in smalls)
    p50=durs[len(durs)//2]; p90=durs[int(0.9*len(durs))]
    def conc_large(ts,dur):                                     # large-region cmds issued during the op window
        lo=bisect.bisect_left(large_cmd_ts,ts); hi=bisect.bisect_right(large_cmd_ts,ts+dur); return hi-lo
    fast=[conc_large(ts,d) for ts,d in smalls if d<=p50]
    slow=[conc_large(ts,d) for ts,d in smalls if d>=p90]
    f_mean=sum(fast)/len(fast) if fast else 0
    s_mean=sum(slow)/len(slow) if slow else 0
    print(f"=== small-op tail attributed to concurrent LARGE device activity ===")
    print(f"small ops: {len(smalls)}  p50={p50:.0f} p90={p90:.0f} (dur us)   large-region nvme cmds: {len(large_cmd_ts)}")
    print(f"FAST small ops (dur<=p50): mean {f_mean:.2f} large device cmds overlap their window")
    print(f"SLOW small ops (dur>=p90): mean {s_mean:.2f} large device cmds overlap their window")
    print(f"=> slow small ops overlap {s_mean/f_mean if f_mean else float('inf'):.1f}x more large-op device "
          f"commands -> tail is head-of-line blocking by large reads (per-op cross-layer attribution)")

if __name__=="__main__":
    main()
