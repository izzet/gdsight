#!/usr/bin/env python3
"""mixed_score.py — necessity test for per-op cross-layer attribution on a heterogeneous
async workload (gds_mixed: class A large-aligned, class B small-unaligned). Shows:
  (1) AGGREGATE device/app ratio (what iostat-vs-app sees) -> looks benign,
  (2) PER-CLASS A_byte via LBA attribution -> reveals class B amplifying,
  (3) corr_id can't do the split (async collapse) -> per-class via corr_id is wrong.
Class is by file offset: < BBASE (1 GiB) = A, >= BBASE = B (gds_mixed layout).

usage: mixed_score.py --trace T --file F
"""
import argparse, bisect, gzip, json, re, subprocess
BBASE = 1<<30
EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')

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
    ps,ls,ln=exts[i]
    return ls+(pb-ps) if pb<ps+ln else None

def cls(off): return 'A' if off<BBASE else 'B'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trace",required=True); ap.add_argument("--file",required=True)
    ap.add_argument("--bs",type=int,default=4096); ap.add_argument("--sec",type=int,default=512)
    a=ap.parse_args()
    exts=extents(a.file); starts=[e[0] for e in exts]
    cufile={}; nvme=[]
    with gzip.open(a.trace,"rt",errors="replace") as f:
        for ln in f:
            ln=ln.strip().rstrip(",")
            if not ln or ln[0]!="{": continue
            try: ev=json.loads(ln)
            except Exception: continue
            n,ar=ev.get("name"),ev.get("args",{})
            if n in ("cuFileReadAsync","cuFileRead","cuFileBatchIOSubmit") and "offset" in ar:
                cufile[int(ar.get("corr_id",-1))]=(int(ar["offset"]),int(ar.get("size",0)))
            elif n=="nvme_setup_cmd" and "sector" in ar:
                nvme.append((int(ar.get("corr_id",-1)),int(ar["sector"]),int(ar.get("size",0))))
    # requested bytes per class (from cuFile API events)
    req={'A':0,'B':0}
    for off,sz in cufile.values(): req[cls(off)]+=sz
    # device bytes per class via LBA (our method); corr_id classification accuracy
    dev={'A':0,'B':0}; cdev={'A':0,'B':0}; corr_ok=0; corr_resolved=0; cmds=0; dev_tot=0
    for cid,sec,sz in nvme:
        lb=p2l(exts,starts,sec*a.sec//a.bs)
        if lb is None: continue
        foff=lb*a.bs
        if foff<0: continue
        c=cls(foff); dev[c]+=sz; dev_tot+=sz; cmds+=1     # cmds = ALL device cmds (the honest denom)
        if cid in cufile:
            corr_resolved+=1
            cc=cls(cufile[cid][0]); cdev[cc]+=sz          # corr_id's (mis)attribution of device bytes
            if cc==c: corr_ok+=1
    reqT=req['A']+req['B']
    def ab(c): return dev[c]/req[c] if req[c] else 0
    print("=== heterogeneous async workload: who can see the class-B pathology? ===")
    print(f"requested  A={req['A']/2**20:6.1f} MiB  B={req['B']/2**20:6.1f} MiB   "
          f"(B is {100*req['B']/reqT:.0f}% of bytes, but most of the OPS)")
    print(f"device     A={dev['A']/2**20:6.1f} MiB  B={dev['B']/2**20:6.1f} MiB")
    print()
    print(f"(1) AGGREGATE device/app  = {dev_tot/reqT:.3f}x   <- what iostat-vs-app sees: looks BENIGN")
    print(f"(2) PER-CLASS via LBA     : A={ab('A'):.3f}x   B={ab('B'):.3f}x   "
          f"(LBA classifies {100*cmds/cmds:.0f}% of {cmds} device cmds)  <- class B PATHOLOGICAL")
    cab=lambda c: cdev[c]/req[c] if req[c] else 0
    print(f"(3) corr_id over ALL cmds : correct {100*corr_ok/cmds if cmds else 0:.1f}% "
          f"({corr_ok}/{cmds}); only {100*corr_resolved/cmds if cmds else 0:.1f}% even resolve to a cuFile op")
    print(f"    corr_id per-class A_byte: A={cab('A'):.3f}x  B={cab('B'):.3f}x  "
          f"(true B={ab('B'):.3f}x) <- corr_id's per-class number is WRONG: under-counts B by "
          f"{100*(1-cab('B')/ab('B')) if ab('B') else 0:.0f}% (mis-billed to A)")

if __name__=="__main__":
    main()
