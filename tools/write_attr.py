#!/usr/bin/env python3
"""write_attr.py -- cross-layer scoring for the GDS write keystone.

Reads a DFTracer .pfw.gz and reports, for a write workload: app-level cuFileWrite volume, whether the
GDS path actually engaged (nvfs_io / p2p mappings), any POSIX fallback, and the device commands split
by direction -- the split being the point, since a misaligned write makes the device issue READ
commands (read-modify-write) that no API-level or GDS-native counter reports.

Device commands are filtered to the workload's own pid. The NVMe on these nodes is shared with other
tenants, so an unfiltered count (or /proc/diskstats) mixes in their traffic.

Usage: write_attr.py <trace.pfw.gz>
"""
import collections
import gzip
import json
import sys


def load(path):
    events = collections.Counter()
    nvme = []          # (op, size, corr_id, pid)
    writes = {}        # corr_id -> (offset, size)
    for line in gzip.open(path, "rt", errors="replace"):
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        name = rec.get("name")
        args = rec.get("args", {})
        events[name] += 1
        if name == "nvme_setup_cmd":
            nvme.append((args.get("op"), args.get("size") or 0, args.get("corr_id"), rec.get("pid")))
        elif name == "cuFileWrite":
            writes[args.get("corr_id")] = (args.get("offset"), args.get("size") or 0)
    return events, nvme, writes


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: write_attr.py <trace.pfw.gz>")
    events, nvme, writes = load(sys.argv[1])
    if not nvme:
        sys.exit("no nvme_setup_cmd events in trace")

    # the workload is whichever pid issued the most device commands
    pid = collections.Counter(p for *_, p in nvme).most_common(1)[0][0]
    mine = [c for c in nvme if c[3] == pid]
    reads = [c for c in mine if c[0] == 0]
    wrs = [c for c in mine if c[0] == 1]
    rbytes = sum(c[1] for c in reads)
    wbytes = sum(c[1] for c in wrs)
    requested = sum(sz for _, sz in writes.values())

    mib = lambda b: b / 1048576.0
    print(f"  app layer      : cuFileWrite={events.get('cuFileWrite', 0)}  "
          f"({mib(requested):.1f} MiB requested)")
    print(f"  GDS layer      : nvfs_io={events.get('nvfs_io', 0)}  "
          f"p2p_mapping={events.get('nvfs_get_p2p_dma_mapping', 0)}")
    print(f"  POSIX fallback : pwrite64={events.get('pwrite64', 0)}  "
          f"fdatasync={events.get('fdatasync', 0)}")
    print(f"  device layer   : {len(mine)} cmds = {len(reads)} READ ({mib(rbytes):.1f} MiB) "
          f"+ {len(wrs)} WRITE ({mib(wbytes):.1f} MiB)")
    if requested:
        print(f"  amplification  : read {rbytes/requested:.2f}x + write {wbytes/requested:.2f}x "
              f"= {(rbytes+wbytes)/requested:.2f}x device bytes per requested byte")
    attributed = sum(1 for c in mine if c[2])
    print(f"  attribution    : {attributed}/{len(mine)} device cmds -> causing cuFileWrite"
          + (f"   (reads {sum(1 for c in reads if c[2])}/{len(reads)})" if reads else ""))


if __name__ == "__main__":
    main()
