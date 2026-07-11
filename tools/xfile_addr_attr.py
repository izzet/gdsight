#!/usr/bin/env python3
"""Multi-file address attribution. Builds one reverse physical->file index over several files
(device phys range -> (file, logical range)); each NVMe command's LBA is looked up in it, so per-op
attribution works across a process reading many files, not just one. This is the general form of the
single-file tools. usage: xfile_addr_attr.py --trace T --file A --file B [...]"""
import argparse, gzip, json, re, subprocess, bisect, os, collections
EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')

def extents(path, fid):
    out = subprocess.run(["filefrag", "-v", path], capture_output=True, text=True).stdout
    r = []
    for ln in out.splitlines():
        m = EXT_RE.match(ln)
        if m:  # (phys_start, phys_end, file_id, logical_start) in fs blocks
            r.append((int(m.group(2)), int(m.group(2)) + int(m.group(3)), fid, int(m.group(1))))
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--file", action="append", required=True)
    ap.add_argument("--bs", type=int, default=4096)
    ap.add_argument("--sec", type=int, default=512)
    a = ap.parse_args()
    names = [os.path.basename(f) for f in a.file]
    R = []
    for fid, f in enumerate(a.file):
        R += extents(f, fid)
    R.sort(); starts = [e[0] for e in R]
    def rev(pb):                       # physical fs block -> (file_id, logical block) or None
        i = bisect.bisect_right(starts, pb) - 1
        if i < 0: return None
        ps, pe, fid, ls = R[i]
        return (fid, ls + (pb - ps)) if pb < pe else None
    per_file = collections.Counter(); unmapped = tot = 0
    with gzip.open(a.trace, "rt", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip().rstrip(",")
            if not ln or ln[0] != "{": continue
            try: e = json.loads(ln)
            except: continue
            if e.get("name") == "nvme_setup_cmd" and "sector" in e.get("args", {}):
                tot += 1
                m = rev(int(e["args"]["sector"]) * a.sec // a.bs)
                if m is None: unmapped += 1
                else: per_file[names[m[0]]] += 1
    print(f"reverse physical->file index: {len(R)} extents over {len(a.file)} files")
    print(f"NVMe commands: {tot}")
    for n in names: print(f"  -> {n}: {per_file[n]} commands")
    print(f"  -> unmapped: {unmapped}")

if __name__ == "__main__":
    main()
