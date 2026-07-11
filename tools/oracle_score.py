#!/usr/bin/env python3
"""oracle_score.py — score corr_id and LBA attribution against an INDEPENDENT per-op
ground-truth oracle. The workload (gds_oracle) reads UNIQUE non-overlapping slots, so a
device command's physical LBA maps (via the file extent map) to exactly one slot = the
true causing op — a ground truth that uses NEITHER corr_id NOR the tracer's timing.

For each nvme_setup_cmd:
  oracle_slot  = file_offset(sector) // slot        (address truth, independent)
  corrid_slot  = offset(cuFileRead[nvme.corr_id]) // slot   (corr_id's claim)
corr_id is correct iff corrid_slot == oracle_slot. LBA-matching maps sector->slot
directly, so it equals the oracle by construction on unique slots (=100%); we still
report it and the ambiguity (fraction of LBAs claimable by >1 op) which is LBA's real
failure mode under overlapping ranges.

usage: oracle_score.py --trace T.pfw.gz --file F --slot 65536 [--label L]
"""
import argparse, bisect, gzip, json, re, subprocess

EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')

def extents(path):
    out = subprocess.run(["filefrag","-v",path],capture_output=True,text=True).stdout
    e=[]
    for ln in out.splitlines():
        m=EXT_RE.match(ln)
        if m: e.append((int(m.group(2)),int(m.group(1)),int(m.group(3))))  # phys,logical,len (fs blocks)
    e.sort(); return e

def phys_to_logical(exts, starts, pb):
    i=bisect.bisect_right(starts,pb)-1
    if i<0: return None
    ps,ls,ln=exts[i]
    return ls+(pb-ps) if pb<ps+ln else None

def parse(path):
    cufile={}; nvme=[]
    with gzip.open(path,"rt",errors="replace") as f:
        for ln in f:
            ln=ln.strip().rstrip(",")
            if not ln or ln[0]!="{": continue
            try: ev=json.loads(ln)
            except Exception: continue
            n,a=ev.get("name"),ev.get("args",{})
            if n in ("cuFileRead","cuFileReadAsync","cuFileBatchIOSubmit") and "offset" in a:
                cufile[int(a.get("corr_id",-1))]=int(a["offset"])
            elif n=="nvme_setup_cmd" and "sector" in a:
                nvme.append((int(a.get("corr_id",-1)),int(a["sector"]),int(a.get("size",0))))
    return cufile,nvme

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trace",required=True); ap.add_argument("--file",required=True)
    ap.add_argument("--slot",type=int,required=True)
    ap.add_argument("--bs",type=int,default=4096); ap.add_argument("--sec",type=int,default=512)
    ap.add_argument("--label",default="")
    args=ap.parse_args()
    exts=extents(args.file); starts=[e[0] for e in exts]
    cufile,nvme=parse(args.trace)
    # how many distinct ops requested each slot -> LBA can uniquely attribute only if ==1
    from collections import Counter
    slot_ops=Counter(off//args.slot for off in cufile.values())  # NOTE: corr_id-keyed, 1 entry/op
    tot=corr_ok=corr_missing=lba_unique=0
    for cid,sector,size in nvme:
        # map every 4 KiB block the command's full [sector*sec, +size) range covers, not just the first
        # sector, so a command straddling two ops' slots resolves to a set (command-to-set), not a
        # false-unique first-block hit. sec/bs are parameters (queried per device), not hardcoded.
        b0=sector*args.sec
        nblocks=max(1,(b0%args.bs+size+args.bs-1)//args.bs) if size else 1
        pb0=b0//args.bs
        slots=set(); mapped=False
        for pb in range(pb0,pb0+nblocks):
            lb=phys_to_logical(exts,starts,pb)
            if lb is None: continue
            mapped=True; slots.add((lb*args.bs)//args.slot)
        if not mapped: continue
        tot+=1
        oslot=next(iter(slots)) if len(slots)==1 else None   # >1 slot => command-to-set
        if oslot is not None and slot_ops.get(oslot,0)==1: lba_unique+=1  # LBA pins the op only if 1 op read this slot
        if cid in cufile:
            if oslot is not None and cufile[cid]//args.slot==oslot: corr_ok+=1
        else:
            corr_missing+=1
    lba_pct=100*lba_unique/tot if tot else 0
    print(f"=== {args.label or args.trace} ===")
    print(f"nvme cmds scored          : {tot}")
    print(f"corr_id CORRECT vs oracle : {corr_ok}/{tot} = {100*corr_ok/tot:.1f}%   "
          f"(no matching cuFileRead: {corr_missing})")
    print(f"LBA uniquely attributable : {lba_unique}/{tot} = {lba_pct:.1f}%   "
          f"(slot read by exactly 1 op; <100% => overlapping-range blind spot)")
    print(f"CSV,{args.label},{tot},{100*corr_ok/tot:.1f},{lba_pct:.1f}")

if __name__=="__main__":
    main()
