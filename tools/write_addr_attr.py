#!/usr/bin/env python3
"""Per-op WRITE attribution by ADDRESS (the write-side mirror of posix_addr_attr.py).

When a library routes sub-threshold writes around cuFile (kvikio's POSIX shortcut), those writes carry
no corr_id, so the time basis cannot see them at all. Worse, if they are misaligned the device must
read whole blocks before writing them back, so a pure-write workload emits READ commands that belong to
no cuFile operation. The address basis resolves both: sector -> FIEMAP -> the individual pwrite or
cuFileWrite whose byte range covers it.

Usage: write_addr_attr.py <trace.pfw.gz> <datafile>
"""
import bisect
import collections
import gzip
import json
import re
import shutil
import subprocess
import sys

SEC = 512
EXT_RE = re.compile(r"^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):")


def extent_map(path):
    """(physical_block_start, logical_block_start, length) sorted by physical, in 4 KiB blocks."""
    exe = shutil.which("filefrag") or "/usr/sbin/filefrag"
    out = subprocess.run([exe, "-v", path], capture_output=True, text=True).stdout
    exts = sorted((int(m.group(2)), int(m.group(1)), int(m.group(3)))
                  for m in (EXT_RE.match(l) for l in out.splitlines()) if m)
    return exts, [e[0] for e in exts]


def main():
    trace, datafile = sys.argv[1], sys.argv[2]
    exts, starts = extent_map(datafile)
    if not exts:
        sys.exit(f"no extents parsed for {datafile}")

    def phys_to_logical(pblk):
        i = bisect.bisect_right(starts, pblk) - 1
        if i < 0:
            return None
        ps, ls, ln = exts[i]
        return (ls + (pblk - ps)) if pblk < ps + ln else None

    ops = []          # (start_byte, end_byte, class)
    cmds = []         # (op_direction, sector, size, pid)
    with gzip.open(trace, "rt", errors="replace") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            n, a = e.get("name"), e.get("args", {})
            if n in ("pwrite", "cuFileWrite") and a.get("size"):
                off = a.get("offset") or 0
                ops.append((off, off + a["size"], "POSIX" if n == "pwrite" else "cuFile"))
            elif n == "nvme_setup_cmd":
                cmds.append((a.get("op"), a.get("sector"), a.get("size") or 0, e.get("pid")))

    if not cmds:
        sys.exit("no device commands in trace")
    pid = collections.Counter(c[3] for c in cmds).most_common(1)[0][0]
    cmds = [c for c in cmds if c[3] == pid]
    ops.sort()
    op_starts = [o[0] for o in ops]

    def attribute(sector):
        lb = phys_to_logical(sector * SEC // 4096)
        if lb is None:
            return None
        # The command covers the whole 4 KiB block [byte, byte+4096). A misaligned write starts INSIDE
        # that block, so its head block begins before the op's offset: test range OVERLAP, not
        # containment of the block start, or every head-block RMW read goes unattributed.
        byte, end = lb * 4096, lb * 4096 + 4096
        i = bisect.bisect_right(op_starts, end) - 1
        while i >= 0 and op_starts[i] > byte - (1 << 20):     # ops are <= 64 KiB, so 1 MiB back is ample
            if ops[i][0] < end and ops[i][1] > byte:
                return ops[i][2]
            i -= 1
        return None

    stats = collections.Counter()
    for direction, sector, _size, _pid in cmds:
        if sector is None:
            continue
        kind = "READ" if direction == 0 else "WRITE"
        cls = attribute(sector)
        stats[(kind, cls or "UNATTRIBUTED")] += 1

    total = sum(stats.values())
    attributed = sum(v for (_k, c), v in stats.items() if c != "UNATTRIBUTED")
    print(f"ops in table       : {len(ops)}  "
          f"({sum(1 for o in ops if o[2]=='POSIX')} POSIX pwrite, "
          f"{sum(1 for o in ops if o[2]=='cuFile')} cuFileWrite)")
    print(f"device commands    : {total}  (workload pid {pid})")
    for (kind, cls), n in sorted(stats.items()):
        print(f"   {kind:<5} -> {cls:<14} {n:>7}")
    print(f"ADDRESS attribution: {attributed}/{total} "
          f"({100.0*attributed/max(total,1):.1f}%)")


if __name__ == "__main__":
    main()
