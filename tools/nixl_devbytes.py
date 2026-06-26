#!/usr/bin/env python3
"""Device bytes attributed to the KV class (region >= 1 GiB) via per-NVMe-command LBA, for the
diagnosis walkthrough. usage: nixl_devbytes.py <trace.pfw.gz> <file> <reqB_bytes>
Prints: devB_MiB  A_byte  nvme_cmds  batches"""
import sys, gzip, json, bisect, subprocess, re
T, F, reqB = sys.argv[1], sys.argv[2], int(sys.argv[3])
BBASE = 1 << 30; BS = 4096; SEC = 512
EXT = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')
ex = []
for ln in subprocess.run(["filefrag", "-v", F], capture_output=True, text=True).stdout.splitlines():
    m = EXT.match(ln)
    if m: ex.append((int(m.group(2)), int(m.group(1)), int(m.group(3))))
ex.sort(); st = [e[0] for e in ex]
def p2l(pb):
    i = bisect.bisect_right(st, pb) - 1
    if i < 0: return None
    ps, ls, ln = ex[i]; return ls + (pb - ps) if pb < ps + ln else None
devB = nvme = batches = 0
for line in gzip.open(T, 'rt', errors='replace'):
    line = line.strip().rstrip(',')
    if not line or line[0] != '{': continue
    try: e = json.loads(line)
    except Exception: continue
    n, a = e.get('name'), e.get('args', {})
    if n == 'cuFileBatchIOSubmit': batches += 1
    elif n == 'nvme_setup_cmd' and 'sector' in a:
        lb = p2l(int(a['sector']) * SEC // BS)
        if lb is None: continue
        if lb * BS >= BBASE: devB += int(a.get('size', 0)); nvme += 1
print(f"{devB/2**20:.2f} {devB/reqB:.3f} {nvme} {batches}")
