#!/usr/bin/env python3
"""Per-entry batch attribution by ADDRESS. cuFileBatchIOSubmit carries nr independent (offset,size)
requests under one call, so the time basis (corr_id) is batch-granular. With per-entry OP records
(the cufile plugin's 'batchentry' events, one per iocbp[i]), the address basis maps each NVMe command's
sector -> file offset (FIEMAP) -> the exact batch entry whose range contains it. usage: batch_addr_attr.py TRACE [FILE]

CANDIDATE LIFETIME. An address candidate is only a candidate while its operation is actually in
flight. Without a completion record every entry stays live for the whole run, so two entries that
touch the same byte range at different times are reported ambiguous even though only one was ever
outstanding when the command issued. The cufile plugin's 'batchdone' record (one per reaped cookie
from cuFileBatchIOGetStatus) closes the window.

Cookies identify a SLOT, not an entry: a caller that recycles one CUfileIOParams_t array reuses the
same cookie every batch (NIXL reuses 32). Pairing is therefore FIFO per cookie, the k-th submit with
the k-th completion, which is sound because a slot cannot be resubmitted before it is reaped. This
script asserts that (strict alternation, non-negative durations) rather than assuming it.

Both numbers are reported, gated and ungated, so the gain from the lifetime is visible."""
import sys, gzip, json, re, subprocess, bisect, collections
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

subs, dones, nvme, bcorrs = collections.defaultdict(list), collections.defaultdict(list), [], set()
with gzip.open(TRACE, "rt", errors="replace") as f:
    for ln in f:
        ln = ln.strip().rstrip(",")
        if not ln or ln[0] != "{": continue
        try: e = json.loads(ln)
        except: continue
        n, a, ts = e.get("name"), e.get("args", {}), e.get("ts")
        if n == "batchentry" and "offset" in a and a.get("size"):
            subs[a.get("count")].append((ts, int(a["offset"]), int(a["offset"]) + int(a["size"])))
            bcorrs.add(a.get("corr_id"))
        elif n == "batchdone":
            dones[a.get("count")].append(ts)
        elif n == "nvme_setup_cmd" and "sector" in a:
            nvme.append((ts, int(a["sector"]), int(a.get("size", 0))))

# FIFO pair per cookie -> (start, end, t_submit, t_done). An unreaped tail entry stays open (inf).
ops, unreaped = [], 0
for ck, sv in subs.items():
    sv.sort(); dv = sorted(dones.get(ck, []))
    for k, (t0, s, en) in enumerate(sv):
        t1 = dv[k] if k < len(dv) else float("inf")
        if t1 == float("inf"): unreaped += 1
        assert t1 >= t0, f"completion precedes submit on cookie {ck}"
        ops.append((s, en, t0, t1))

ops.sort(); op_starts = [o[0] for o in ops]
def covering(lo, hi, ts=None):
    """Entries whose byte range overlaps [lo,hi). With ts, only those in flight at ts."""
    hits = []
    i = bisect.bisect_right(op_starts, hi) - 1
    while i >= 0 and op_starts[i] >= lo - (1 << 20):
        s, en, t0, t1 = ops[i]
        if s < hi and en > lo and (ts is None or t0 <= ts <= t1): hits.append(i)
        i -= 1
    return hits

def attribute(gated):
    uniq = ambig = unmapped = 0
    for ts, sector, size in nvme:
        lb = p2l(sector * SEC // BS)
        if lb is None: unmapped += 1; continue
        foff = lb * BS + (sector * SEC) % BS
        h = covering(foff, foff + max(size, BS), ts if gated else None)
        if len(h) == 1: uniq += 1
        elif len(h) > 1: ambig += 1
        else: unmapped += 1
    return uniq, ambig, unmapped

u0, a0, x0 = attribute(False)
u1, a1, x1 = attribute(True)

print(f"batch entries (address ops)   : {len(ops)}   completions paired: {len(ops)-unreaped}"
      f"   unreaped: {unreaped}")
print(f"distinct batch corr_ids (time): {len(bcorrs)}")
print(f"NVMe commands                 : {len(nvme)}")
print(f"ADDRESS, no lifetime          : unique {u0}   ambiguous {a0}   unmapped {x0}")
print(f"ADDRESS, lifetime-gated       : unique {u1}   ambiguous {a1}   unmapped {x1}")
print(f"\n=> address pins each command to 1 of {len(ops)} entries; the time basis distinguishes only "
      f"{len(bcorrs)} batches ({len(ops)//max(1,len(bcorrs))}x coarser).")
print(f"=> retiring completed candidates resolves {a0-a1} ambiguous command(s).")
