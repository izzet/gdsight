#!/usr/bin/env python3
"""rmw_proof.py -- is the device read traffic under a write workload really read-modify-write?

"A write workload issues device reads" is only interesting if the reads are RMW of the very blocks
being written. Alternatives that would make the claim wrong: filesystem metadata reads (bitmaps,
inode/extent blocks), readahead, or another tenant's traffic on the shared NVMe.

Uses the per-op corr_id the tracer already assigns: group every device command by the cuFileWrite that
caused it, then ask whether a READ and a WRITE within the same op touch the same sector. Same-op,
same-sector read-then-write IS read-modify-write, and nothing else produces that signature.

Usage: rmw_proof.py <trace.pfw.gz> [datafile-for-extent-check]
"""
import collections
import gzip
import json
import shutil
import subprocess
import sys


def load(path):
    cmds = []   # (corr_id, op, sector, size, ts, pid)
    writes = {}
    for line in gzip.open(path, "rt", errors="replace"):
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        a = rec.get("args", {})
        if rec.get("name") == "nvme_setup_cmd":
            cmds.append((a.get("corr_id"), a.get("op"), a.get("sector"), a.get("size") or 0,
                         rec.get("ts"), rec.get("pid")))
        elif rec.get("name") == "cuFileWrite":
            writes[a.get("corr_id")] = (a.get("offset"), a.get("size") or 0)
    return cmds, writes


def extents(path):
    """Physical sector ranges of the file, via filefrag -b512. Empty on failure."""
    exe = shutil.which("filefrag") or "/usr/sbin/filefrag"  # not on a non-root PATH
    try:
        out = subprocess.run([exe, "-b512", "-e", path], capture_output=True, text=True,
                             timeout=60).stdout
    except Exception:
        return []
    rngs = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(":")]
        if len(parts) >= 4 and parts[0].isdigit():
            phys = parts[2].split("..")   # ext: logical: PHYSICAL: length: flags
            try:
                rngs.append((int(phys[0]), int(phys[1].split()[0])))
            except (ValueError, IndexError):
                pass
    return rngs


def main():
    cmds, writes = load(sys.argv[1])
    if not cmds:
        sys.exit("no device commands in trace")
    pid = collections.Counter(c[5] for c in cmds).most_common(1)[0][0]
    mine = [c for c in cmds if c[5] == pid]
    reads = [c for c in mine if c[1] == 0]
    print(f"device commands (workload pid {pid}): {len(mine)}  reads={len(reads)}  "
          f"writes={sum(1 for c in mine if c[1] == 1)}")

    # --- 1. same-op, same-sector read+write == read-modify-write ---
    by_op = collections.defaultdict(list)
    for c in mine:
        if c[0]:
            by_op[c[0]].append(c)
    rmw_ops = paired_reads = 0
    for op_cmds in by_op.values():
        rsect = {c[2] for c in op_cmds if c[1] == 0}
        wsect = {c[2] for c in op_cmds if c[1] == 1}
        both = rsect & wsect
        if both:
            rmw_ops += 1
            paired_reads += sum(1 for c in op_cmds if c[1] == 0 and c[2] in both)
    print(f"\n1. RMW signature (same op, same sector, read AND write)")
    print(f"   ops exhibiting it : {rmw_ops}/{len(by_op)} ({100.0*rmw_ops/max(len(by_op),1):.1f}%)")
    print(f"   reads so paired   : {paired_reads}/{len(reads)} "
          f"({100.0*paired_reads/max(len(reads),1):.1f}%)")

    # --- 2. do the reads land in the data file, or are they metadata? ---
    if len(sys.argv) > 2:
        rngs = extents(sys.argv[2])
        if rngs:
            def inside(s):
                return any(lo <= s <= hi for lo, hi in rngs)
            hit = sum(1 for c in reads if c[2] is not None and inside(c[2]))
            print(f"\n2. Read commands inside the data file's extents ({len(rngs)} extents)")
            print(f"   in-file : {hit}/{len(reads)} ({100.0*hit/max(len(reads),1):.1f}%)  "
                  f"-> remainder is metadata/other")
        else:
            print("\n2. extent check skipped (filefrag unavailable or no extents parsed)")

    # --- 3. read sizes: RMW of a 4 KiB FS block should be 4 KiB ---
    sz = collections.Counter(c[3] for c in reads)
    print("\n3. Read command sizes")
    for s, n in sz.most_common(4):
        print(f"   {s:>8} B : {n}")


if __name__ == "__main__":
    main()
