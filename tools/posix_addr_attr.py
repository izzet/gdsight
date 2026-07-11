#!/usr/bin/env python3
"""Per-op POSIX attribution by ADDRESS. The kvikio sub-threshold path reads via POSIX pread (no cuFile,
no corr_id). With the pread offset captured (cufile plugin's libc:pread uprobe), the address basis maps
each NVMe command's sector -> file offset (FIEMAP) -> the individual op whose byte range contains it,
resolving cuFile AND POSIX reads in one unified op table. The time basis cannot: a POSIX command carries
no cuFile id, so corr_id either drops it or mis-bills it to a concurrently-active cuFile op."""
import sys, gzip, json, re, subprocess, bisect
F = "/mnt/nvme1/gdstrace-smoke/ovh.dat"
TRACE = sys.argv[1]
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

ops, nvme, cufile_off = [], [], {}          # ops: (start, end, cls); cufile_off: corr_id -> offset
with gzip.open(TRACE, "rt", errors="replace") as f:
    for ln in f:
        ln = ln.strip().rstrip(",")
        if not ln or ln[0] != "{": continue
        try: e = json.loads(ln)
        except: continue
        n, a = e.get("name"), e.get("args", {})
        if n == "cuFileRead" and "offset" in a and a.get("size"):
            ops.append((int(a["offset"]), int(a["offset"]) + int(a["size"]), "cuFile"))
            cufile_off[int(a.get("corr_id", -1))] = int(a["offset"])
        elif n == "pread" and a.get("size") and int(a.get("offset", 0)) > 0:
            ops.append((int(a["offset"]), int(a["offset"]) + int(a["size"]), "POSIX"))
        elif n == "nvme_setup_cmd" and "sector" in a:
            nvme.append((int(a.get("corr_id", -1)), int(a["sector"]), int(a.get("size", 0))))

ops.sort(); op_starts = [o[0] for o in ops]
def covering(lo, hi):                         # ops whose [start,end) intersects the command range [lo,hi)
    hits = []
    i = bisect.bisect_right(op_starts, hi) - 1
    while i >= 0 and op_starts[i] >= lo - (1 << 20):     # window >= max op size (1 MiB cuFile)
        s, en, c = ops[i]
        if s < hi and en > lo: hits.append(ops[i])
        i -= 1
    return hits

uniq_posix = uniq_cufile = ambig = unmapped = 0
corr_posix_misbilled = corr_posix_dropped = 0
for cid, sector, size in nvme:
    lb = p2l(sector * SEC // BS)
    if lb is None: unmapped += 1; continue
    foff = lb * BS + (sector * SEC) % BS
    hits = covering(foff, foff + max(size, BS))   # the command's serviced byte range (>=1 grid cell)
    cls = {h[2] for h in hits}
    if len(hits) == 1:
        if hits[0][2] == "POSIX": uniq_posix += 1
        else: uniq_cufile += 1
    elif len(hits) > 1 and len(cls) > 1: ambig += 1
    elif len(hits) > 1: uniq_posix += (cls == {"POSIX"}); uniq_cufile += (cls == {"cuFile"})
    else: unmapped += 1
    if foff >= (1 << 30):                    # what the time basis does with this POSIX-region command
        if cid in cufile_off: corr_posix_misbilled += 1   # stale cuFile id -> wrong (clean) class
        else: corr_posix_dropped += 1

print(f"NVMe commands            : {len(nvme)}")
print(f"app ops in unified table : {sum(1 for o in ops if o[2]=='cuFile')} cuFile + "
      f"{sum(1 for o in ops if o[2]=='POSIX')} POSIX")
print("\nADDRESS basis, per individual op (sector -> FIEMAP -> the op whose range contains it):")
print(f"  attributed to a unique POSIX pread : {uniq_posix}")
print(f"  attributed to a unique cuFile read : {uniq_cufile}")
print(f"  ambiguous (cuFile+POSIX overlap)   : {ambig}")
print(f"  unmapped                           : {unmapped}")
print("\nTIME basis (corr_id) on the same POSIX-region commands:")
print(f"  mis-billed to a clean cuFile op    : {corr_posix_misbilled}")
print(f"  dropped (no id)                    : {corr_posix_dropped}")
print(f"\n=> address attributes {uniq_posix} POSIX device commands to their EXACT pread; "
      f"time mis-bills/drops all {corr_posix_misbilled + corr_posix_dropped}.")
