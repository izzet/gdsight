#!/usr/bin/env python3
"""Sound composition: address for unique coverage; time breaks an overlap tie ONLY when the temporal key is a
same-thread (provably valid) owner (sync=1); otherwise command-to-set. Contrast with the NAIVE compose that
trusts any t in the address candidate set -- which an aliased key can break wrongly. The sync bit is emitted
per NVMe command by the block probe. usage: compose_score.py --trace T --file F --slot 65536 [--label L]"""
import argparse, gzip, json, re, subprocess, bisect
from collections import Counter
EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')

def extents(p):
    out = subprocess.run(["filefrag", "-v", p], capture_output=True, text=True).stdout
    e = [(int(m.group(2)), int(m.group(1)), int(m.group(3)))
         for m in (EXT_RE.match(l) for l in out.splitlines()) if m]
    e.sort(); return e

def p2l(e, st, pb):
    i = bisect.bisect_right(st, pb) - 1
    if i < 0: return None
    ps, ls, ln = e[i]
    return ls + (pb - ps) if pb < ps + ln else None

ap = argparse.ArgumentParser()
ap.add_argument("--trace", required=True); ap.add_argument("--file", required=True)
ap.add_argument("--slot", type=int, required=True); ap.add_argument("--label", default="")
ap.add_argument("--bs", type=int, default=4096); ap.add_argument("--sec", type=int, default=512)
a = ap.parse_args()
e = extents(a.file); st = [x[0] for x in e]
cufile, nvme = {}, []
with gzip.open(a.trace, "rt", errors="replace") as f:
    for ln in f:
        ln = ln.strip().rstrip(",")
        if not ln or ln[0] != "{": continue
        try: ev = json.loads(ln)
        except: continue
        n, ar = ev.get("name"), ev.get("args", {})
        if n in ("cuFileRead", "cuFileReadAsync", "cuFileBatchIOSubmit", "batchentry") and "offset" in ar:
            cufile[int(ar.get("corr_id", -1))] = int(ar["offset"])
        elif n == "nvme_setup_cmd" and "sector" in ar:
            nvme.append((int(ar.get("corr_id", -1)), int(ar.get("sync", 0)), int(ar["sector"])))
slot_ops = Counter(off // a.slot for off in cufile.values())
uniq = time_tie = cts = 0
naive_single = sound_single = 0
for cid, sync, sector in nvme:
    lb = p2l(e, st, sector * a.sec // a.bs)
    if lb is None: continue
    oslot = (lb * a.bs) // a.slot
    n_ops = slot_ops.get(oslot, 0)                       # |address candidate set| for the oracle
    t_in_a = (cid in cufile) and (cufile[cid] // a.slot == oslot)
    if n_ops == 1:
        uniq += 1                                        # unique coverage -> address
    else:                                                # overlap
        if t_in_a: naive_single += 1                     # NAIVE would return this single op (aliased-key risk)
        if sync and t_in_a:
            time_tie += 1; sound_single += 1             # SOUND: same-thread valid -> time breaks the tie
        else:
            cts += 1                                     # SOUND: absent/aliased -> command-to-set
tot = uniq + time_tie + cts
syncs = sum(1 for _, s, _ in nvme if s == 1)
print(f"=== {a.label or a.trace} ===")
print(f"NVMe cmds: {len(nvme)} | sync=1 (same-thread valid): {syncs} | unique-coverage: {uniq} | overlap: {len(nvme)-uniq}")
print(f"SOUND compose: address(unique)={uniq}  valid-time-tiebreak={time_tie}  command-to-set={cts}")
print(f"NAIVE would return a single op for {naive_single} overlap cmds; SOUND returns single op for {sound_single} "
      f"(sync=1) -> {naive_single - sound_single} would-be aliased wrong attributions become command-to-set.")
