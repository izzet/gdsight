#!/usr/bin/env python3
"""keystone_gds_score.py — per-class attribution for a DIRECT-GDS heterogeneous workload
(all reads via cuFile, no kvikio threshold bypass), e.g. the NIXL-sized keystone from
gds_mixed.c. Class by file region: A < BBASE (1 GiB), B >= BBASE. Reports aggregate vs
per-class device amplification (LBA) and corr_id reliability on class B. Unlike
mixed_score.py (built for the kvikio POSIX-bypass case), requested bytes come from the
cuFile events themselves, since here B does enter cuFile.
usage: keystone_gds_score.py <trace.pfw.gz> <file>
"""
import sys, gzip, json, bisect, subprocess, re
BBASE = 1 << 30; BS = 4096; SEC = 512
EXT = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')

def main():
    T, F = sys.argv[1], sys.argv[2]
    ex = []
    for ln in subprocess.run(["filefrag","-v",F],capture_output=True,text=True).stdout.splitlines():
        m = EXT.match(ln)
        if m: ex.append((int(m.group(2)),int(m.group(1)),int(m.group(3))))
    ex.sort(); st = [e[0] for e in ex]
    def p2l(pb):
        i = bisect.bisect_right(st, pb) - 1
        if i < 0: return None
        ps, ls, ln = ex[i]; return ls + (pb - ps) if pb < ps + ln else None
    cufile = {}; nvme = []
    for line in gzip.open(T, 'rt', errors='replace'):
        line = line.strip().rstrip(',')
        if not line or line[0] != '{': continue
        try: e = json.loads(line)
        except Exception: continue
        n, a = e.get('name'), e.get('args', {})
        if n in ('cuFileReadAsync','cuFileRead') and 'offset' in a:
            cufile[int(a.get('corr_id',-1))] = (int(a['offset']), int(a.get('size',0)))
        elif n == 'nvme_setup_cmd' and 'sector' in a:
            nvme.append((int(a.get('corr_id',-1)), int(a['sector']), int(a.get('size',0))))
    cls = lambda o: 'A' if o < BBASE else 'B'
    req = {'A':0,'B':0}; ops = {'A':0,'B':0}
    for off, sz in cufile.values(): req[cls(off)] += sz; ops[cls(off)] += 1
    dev = {'A':0,'B':0}; bcmd = bok = bwrong = bnone = 0
    for cid, sec, sz in nvme:
        lb = p2l(sec*SEC//BS)
        if lb is None: continue
        c = cls(lb*BS); dev[c] += sz
        if c == 'B':
            bcmd += 1
            if cid in cufile: bok += (cls(cufile[cid][0])=='B'); bwrong += (cls(cufile[cid][0])=='A')
            else: bnone += 1
    print(f"cuFileRead(Async) ops: A={ops['A']} B={ops['B']}  (all via GDS, no bypass)")
    print(f"requested  A={req['A']/2**20:.1f}MiB  B={req['B']/2**20:.2f}MiB")
    print(f"device     A={dev['A']/2**20:.1f}MiB  B={dev['B']/2**20:.2f}MiB")
    print(f"(1) AGGREGATE device/app = {(dev['A']+dev['B'])/(req['A']+req['B']):.3f}x  (benign)")
    print(f"(2) PER-CLASS via LBA    : A={dev['A']/req['A']:.3f}x  B={dev['B']/req['B']:.3f}x  (class B amplifies)")
    print(f"(3) corr_id on B ({bcmd} cmds): {100*bok/bcmd:.0f}% correct, {100*bnone/bcmd:.0f}% unattributed, "
          f"{100*bwrong/bcmd:.0f}% mis-billed  -> unreliable under async")

if __name__ == "__main__":
    main()
