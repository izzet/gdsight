#!/usr/bin/env python3
"""Per-entry batch attribution by ADDRESS. cuFileBatchIOSubmit carries nr independent (offset,size)
requests under one call, so the time basis (corr_id) is batch-granular. With per-entry OP records
(the cufile plugin's 'batchentry' events, one per iocbp[i]), the address basis maps each NVMe command's
sector -> file offset (FIEMAP) -> the exact batch entry whose range contains it. usage: batch_addr_attr.py TRACE [FILE]"""
import sys, gzip, json, re, subprocess, bisect
TRACE = sys.argv[1]
F = sys.argv[2] if len(sys.argv) > 2 else "/mnt/nvme1/gdstrace-smoke/ovh.dat"
BS, SEC = 4096, 512
EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')
out = subprocess.run(["filefrag", "-v", F], capture_output=True, text=True).stdout
exts = sorted((int(m.group(2)), int(m.group(1)), int(m.group(3)))
              for m in (EXT_RE.match(l) for l in out.splitlines()) if m)
starts = [e[0] for e in exts]
def p2l(pb):
    i = bisect.bisect_right(starts, pb) - 1
    if i < 0: return None
    ps, ls, ln = exts[i]
    return ls + (pb - ps) if pb < ps + ln else None

ops, nvme, bcorrs = [], [], set()
with gzip.open(TRACE, "rt", errors="replace") as f:
    for ln in f:
        ln = ln.strip().rstrip(",")
        if not ln or ln[0] != "{": continue
        try: e = json.loads(ln)
        except: continue
        n, a = e.get("name"), e.get("args", {})
        if n == "batchentry" and "offset" in a and a.get("size"):
            ops.append((int(a["offset"]), int(a["offset"]) + int(a["size"])))
            bcorrs.add(a.get("corr_id"))
        elif n == "nvme_setup_cmd" and "sector" in a:
            nvme.append((int(a["sector"]), int(a.get("size", 0))))

ops.sort(); op_starts = [o[0] for o in ops]
def covering(lo, hi):
    hits = []
    i = bisect.bisect_right(op_starts, hi) - 1
    while i >= 0 and op_starts[i] >= lo - (1 << 20):
        s, en = ops[i]
        if s < hi and en > lo: hits.append(i)
        i -= 1
    return hits

uniq = ambig = unmapped = 0
for sector, size in nvme:
    lb = p2l(sector * SEC // BS)
    if lb is None: unmapped += 1; continue
    foff = lb * BS + (sector * SEC) % BS
    h = covering(foff, foff + max(size, BS))
    if len(h) == 1: uniq += 1
    elif len(h) > 1: ambig += 1
    else: unmapped += 1

print(f"batch entries (address ops)   : {len(ops)}")
print(f"distinct batch corr_ids (time): {len(bcorrs)}")
print(f"NVMe commands                 : {len(nvme)}")
print(f"ADDRESS -> unique batch entry : {uniq}   ambiguous: {ambig}   unmapped: {unmapped}")
print(f"\n=> address pins each command to 1 of {len(ops)} entries; the time basis distinguishes only "
      f"{len(bcorrs)} batches ({len(ops)//max(1,len(bcorrs))}x coarser).")
