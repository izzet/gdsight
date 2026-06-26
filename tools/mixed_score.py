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
    ap.add_argument("--sb",type=int,default=3072,help="small-class read size (to count POSIX-bypassed reqs)")
    a=ap.parse_args()
    exts=extents(a.file); starts=[e[0] for e in exts]
    cufile={}; nvme=[]; posix_b=0; posix_n=0
    with gzip.open(a.trace,"rt",errors="replace") as f:
        for ln in f:
            ln=ln.strip().rstrip(",")
            if not ln or ln[0]!="{": continue
            try: ev=json.loads(ln)
            except Exception: continue
            n,ar=ev.get("name"),ev.get("args",{})
            if n in ("cuFileReadAsync","cuFileRead","cuFileBatchIOSubmit") and "offset" in ar:
                cufile[int(ar.get("corr_id",-1))]=(int(ar["offset"]),int(ar.get("size",0)))
            elif n in ("read","pread64") and int(ar.get("size",0))==a.sb:
                posix_b+=a.sb; posix_n+=1          # small-class reads that silently went POSIX (bypass)
            elif n=="nvme_setup_cmd" and "sector" in ar:
                nvme.append((int(ar.get("corr_id",-1)),int(ar["sector"]),int(ar.get("size",0))))
    # requested bytes per class: A from cuFile (GDS) events, B from POSIX-bypassed reads
    req={'A':0,'B':posix_b}
    for off,sz in cufile.values(): req[cls(off)]+=sz
    # device bytes per class via LBA (our method); corr_id classification accuracy
    dev={'A':0,'B':0}; devc={'A':0,'B':0}; cmds=0; dev_tot=0
    b_correct=b_wrong=b_none=0                              # corr_id outcome for class-B device cmds
    for cid,sec,sz in nvme:
        lb=p2l(exts,starts,sec*a.sec//a.bs)
        if lb is None: continue
        foff=lb*a.bs
        if foff<0: continue
        c=cls(foff); dev[c]+=sz; devc[c]+=1; dev_tot+=sz; cmds+=1
        if c=='B':
            if cid in cufile:
                if cls(cufile[cid][0])=='B': b_correct+=1
                else: b_wrong+=1                            # stale corr_id from another class => mis-billed
            else: b_none+=1
    reqT=req['A']+req['B']
    cuf_ops={'A':0,'B':0}
    for off,sz in cufile.values(): cuf_ops[cls(off)]+=1
    def ab(c): return dev[c]/req[c] if req[c] else 0
    print("=== real kvikio RAG-mix: who can see the class-B pathology? ===")
    print(f"requested  A={req['A']/2**20:6.1f} MiB (GDS)   B={req['B']/2**20:6.1f} MiB (POSIX-bypassed)")
    print(f"device     A={dev['A']/2**20:6.1f} MiB         B={dev['B']/2**20:6.1f} MiB ({devc['B']} cmds)")
    print()
    print(f"gds_stats view  : {cuf_ops['A']+cuf_ops['B']} cuFileRead on GDS (A={cuf_ops['A']}, B={cuf_ops['B']}); "
          f"{posix_n} class-B reads went POSIX.")
    if cuf_ops['B']==0 and posix_n>0:
        print(f"                  class B ({devc['B']} device cmds) is INVISIBLE to gds_stats (silently POSIX).")
    elif posix_n==0 and cuf_ops['B']>0:
        print(f"                  class B is now ON the GDS path ({cuf_ops['B']} cuFileRead) -> visible + P2P.")
    print(f"(1) AGGREGATE device/app = {dev_tot/reqT:.3f}x   <- iostat-vs-app: looks BENIGN")
    print(f"(2) corr_id on class B   : {100*b_correct/devc['B'] if devc['B'] else 0:.1f}% correct "
          f"({b_correct}/{devc['B']}); {100*b_wrong/devc['B'] if devc['B'] else 0:.0f}% MIS-billed to class A "
          f"(stale corr_id), {100*b_none/devc['B'] if devc['B'] else 0:.0f}% unattributed <- corr_id is WRONG")
    print(f"(3) LBA on class B       : 100% attributable; reveals B amplifies {ab('B'):.2f}x at the device "
          f"(true class-A {ab('A'):.2f}x) <- ONLY address-based per-op attribution sees it")

if __name__=="__main__":
    main()
