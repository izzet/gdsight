#!/usr/bin/env python3
"""xlayer_amp.py — per-operation cross-layer amplification from a DataCrumbs trace.

For each cuFileRead (corr_id, size), attribute every nvme_setup_cmd sharing that
corr_id and measure how one application read transforms into device commands+bytes:
  A_cmd  = #nvme_cmds attributed to the cuFile op   (command amplification)
  A_byte = Σ(nvme bytes) / cuFile bytes             (byte amplification)
Also reports the device command-size distribution (effective MDTS), unattributed
device commands (readahead/other), and the cuFile->nvfs->nvme reach.

This is the *instrument* for Study A/B (cross-layer amplification law): run it over a
size×pattern×path sweep; the law is A_cmd(S) ~ ceil(S/MDTS), and the *findings* are the
deviations (extra cmds, sub-request byte inflation, GDS-vs-bounce stream divergence).

Usage: xlayer_amp.py <trace.pfw.gz> [label]
"""
import sys, gzip, json, statistics as st

def load(path):
    cufile, nvme, nvfs = {}, [], 0
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as f:
        for ln in f:
            ln = ln.strip().rstrip(",")
            if not ln or ln[0] != "{":
                continue
            try:
                e = json.loads(ln)
            except Exception:
                continue
            n = e.get("name"); a = e.get("args", {})
            if n == "cuFileRead":
                cid = a.get("corr_id")
                if cid is not None:
                    cufile[cid] = {"size": a.get("size", 0), "dur": e.get("dur", 0)}
            elif n == "nvme_setup_cmd":
                nvme.append((a.get("corr_id"), a.get("size", 0), a.get("sector")))
            elif n == "nvfs_io":
                nvfs += 1
    return cufile, nvme, nvfs

def main():
    path = sys.argv[1]; label = sys.argv[2] if len(sys.argv) > 2 else path.split("/")[-1]
    cufile, nvme, nvfs = load(path)
    # attribute nvme cmds to cuFile ops by corr_id
    per = {cid: {"cmds": 0, "bytes": 0} for cid in cufile}
    cmd_sizes = []; unattributed = 0; unattr_bytes = 0
    for cid, sz, sec in nvme:
        cmd_sizes.append(sz)
        if cid in per:
            per[cid]["cmds"] += 1; per[cid]["bytes"] += sz
        else:
            unattributed += 1; unattr_bytes += sz
    n_ops = len(cufile)
    cufile_bytes = sum(v["size"] for v in cufile.values())
    A_cmd = [per[c]["cmds"] for c in cufile]
    A_byte = [per[c]["bytes"] / cufile[c]["size"] for c in cufile if cufile[c]["size"]]
    matched_nvme_bytes = sum(per[c]["bytes"] for c in cufile)
    def q(xs, f=st.median):
        return f(xs) if xs else 0
    print(f"=== {label} ===")
    print(f"cuFile ops          : {n_ops}   cuFile bytes: {cufile_bytes/2**20:.1f} MiB")
    print(f"nvme cmds (total)   : {len(nvme)}   attributed: {len(nvme)-unattributed}   "
          f"unattributed: {unattributed} ({unattr_bytes/2**20:.1f} MiB)")
    print(f"nvfs_io events      : {nvfs}")
    if A_cmd:
        print(f"A_cmd  per op       : mean {st.mean(A_cmd):.2f}  median {q(A_cmd):.0f}  "
              f"min {min(A_cmd)}  max {max(A_cmd)}")
    if A_byte:
        print(f"A_byte per op       : mean {st.mean(A_byte):.3f}  median {q(A_byte):.3f}  "
              f"max {max(A_byte):.3f}   (aggregate {matched_nvme_bytes/cufile_bytes:.3f})")
    if cmd_sizes:
        print(f"nvme cmd size (KiB) : median {q(cmd_sizes)/1024:.0f}  max {max(cmd_sizes)/1024:.0f}"
              f"  (=effective max device transfer / MDTS proxy)")
    # one-line CSV row for the sweep table
    print(f"CSV,{label},{n_ops},{st.mean(A_cmd) if A_cmd else 0:.3f},"
          f"{st.mean(A_byte) if A_byte else 0:.4f},{(max(cmd_sizes)/1024) if cmd_sizes else 0:.0f},"
          f"{unattributed}")

if __name__ == "__main__":
    main()
