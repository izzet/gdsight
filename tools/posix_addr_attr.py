#!/usr/bin/env python3
"""#8 demonstration: the ADDRESS basis attributes the POSIX-bypassed class where corr_id (time) cannot.
Each NVMe command's sector -> file offset (FIEMAP) -> region (A: cuFile/GDS <1GiB, B: POSIX pread >=1GiB).
corr_id can only tag commands that carry a live cuFile id; the POSIX-driven class-B commands have none."""
import sys, gzip, json, re, subprocess, bisect, collections
BBASE = 1 << 30
F = "/mnt/nvme1/gdstrace-smoke/ovh.dat"
TRACE = sys.argv[1]
BS, SEC = 4096, 512
EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')
out = subprocess.run(["filefrag","-v",F], capture_output=True, text=True).stdout
exts = sorted((int(m.group(2)),int(m.group(1)),int(m.group(3)))
              for m in (EXT_RE.match(l) for l in out.splitlines()) if m)
starts = [e[0] for e in exts]
def p2l(pb):
    i = bisect.bisect_right(starts, pb) - 1
    if i < 0: return None
    ps, ls, ln = exts[i]
    return ls + (pb - ps) if pb < ps + ln else None
cufile, nvme, posix_n = {}, [], 0
with gzip.open(TRACE, "rt", errors="replace") as f:
    for ln in f:
        ln = ln.strip().rstrip(",")
        if not ln or ln[0] != "{": continue
        try: e = json.loads(ln)
        except: continue
        n, a = e.get("name"), e.get("args", {})
        if n == "cuFileRead" and "offset" in a: cufile[int(a.get("corr_id",-1))] = int(a["offset"])
        elif n == "pread64" and int(a.get("size",0)) == 3072: posix_n += 1
        elif n == "nvme_setup_cmd" and "sector" in a:
            nvme.append((int(a.get("corr_id",-1)), int(a["sector"]), int(a.get("size",0))))
addr = collections.Counter(); corr_attr = collections.Counter(); corr_unattr_by_region = collections.Counter()
for cid, sector, size in nvme:
    lb = p2l(sector*SEC//BS)
    if lb is None: continue
    foff = lb*BS
    region = 'B(POSIX-bypass)' if foff >= BBASE else 'A(cuFile/GDS)'
    addr[region] += 1
    if cid in cufile:
        corr_attr['A(cuFile/GDS)' if cufile[cid] < BBASE else 'B(POSIX-bypass)'] += 1
    else:
        corr_unattr_by_region[region] += 1
print(f"class-B POSIX pread64(3072B) ops in trace : {posix_n}")
print(f"NVMe commands total                       : {len(nvme)}")
print("\nADDRESS basis (sector -> FIEMAP -> region):")
for k in sorted(addr): print(f"  {k:18s}: {addr[k]} commands  <- attributed by address")
print("\ncorr_id (time) basis:")
tot_attr = sum(corr_attr.values())
for k in sorted(corr_attr): print(f"  attributed -> {k:18s}: {corr_attr[k]}")
for k in sorted(corr_unattr_by_region): print(f"  UNATTRIBUTED (no live cuFile id), truly {k:18s}: {corr_unattr_by_region[k]}")
b_cmds = addr.get('B(POSIX-bypass)',0)
print(f"\n=> address attributes the {b_cmds} POSIX-bypassed class-B commands; corr_id leaves "
      f"{corr_unattr_by_region.get('B(POSIX-bypass)',0)} of them unattributed (POSIX carries no cuFile corr_id).")
