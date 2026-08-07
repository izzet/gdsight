#!/usr/bin/env python3
"""Classify every nvme_setup_cmd in a trace against a file's extent map, to account for the gap
between an operation count and a device command count.

A device command count is almost never equal to the operation count, and the reasons differ by
workload. This prints the breakdown so the difference can be stated rather than waved at:
  in-file vs out-of-file   commands whose sector does not reverse-map into this file are not the
                           workload's at all (other tenants, the ext4 journal, block-layer kworkers)
  read vs write            a write in a pure-read workload is by definition not the workload's
  by issuing process       foreign traffic separates cleanly by pid
  size histogram           large commands pair with data transfers, small ones are usually the
                           format's own metadata (superblock, object header, index nodes)

usage: classify_commands.py TRACE FILE [--split-bytes N]
"""
import sys, gzip, json, re, subprocess, bisect, collections, argparse

ap = argparse.ArgumentParser()
ap.add_argument("trace"); ap.add_argument("file")
ap.add_argument("--split-bytes", type=int, default=200000,
                help="commands at or above this are 'large' (data), below are 'small' (metadata)")
a = ap.parse_args()

BS, SEC = 4096, 512
EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')
out = subprocess.run(["filefrag", "-v", a.file], capture_output=True, text=True).stdout
exts = sorted((int(m.group(2)), int(m.group(1)), int(m.group(3)))
              for m in (EXT_RE.match(l) for l in out.splitlines()) if m)
if not exts:
    sys.exit(f"no extents for {a.file} (filefrag failed?)")
starts = [e[0] for e in exts]

def p2l(pb):
    i = bisect.bisect_right(starts, pb) - 1
    if i < 0: return None
    ps, ls, ln = exts[i]
    return ls + (pb - ps) if pb < ps + ln else None

cmds, layers = [], collections.Counter()
with gzip.open(a.trace, "rt", errors="replace") as f:
    for ln in f:
        ln = ln.strip().rstrip(",")
        if not ln or ln[0] != "{": continue
        try: e = json.loads(ln)
        except: continue
        n = e.get("name")
        if n in ("cuFileRead", "cuFileWrite", "cuFileReadAsync", "batchentry", "nvfs_io",
                 "nvfs_get_p2p_dma_mapping", "pread", "pwrite"):
            layers[n] += 1
        elif n == "nvme_setup_cmd":
            arg = e.get("args", {})
            sec = int(arg.get("sector", 0))
            cmds.append({"pid": e.get("pid"), "op": arg.get("op"), "size": int(arg.get("size", 0)),
                         "sector": sec, "off": (lambda lb: None if lb is None else lb * BS + (sec * SEC) % BS)(p2l(sec * SEC // BS))})

inn = [c for c in cmds if c["off"] is not None]
outn = [c for c in cmds if c["off"] is None]
big = [c for c in inn if c["size"] >= a.split_bytes]
small = [c for c in inn if c["size"] < a.split_bytes]

print(f"trace : {a.trace}")
print(f"file  : {a.file}")
print("\nupper layers: " + "  ".join(f"{k}={v}" for k, v in sorted(layers.items())))
print(f"\nnvme_setup_cmd total : {len(cmds)}")
print(f"  in this file's extents  : {len(inn)}")
print(f"  NOT in this file        : {len(outn)}   <- not the workload's")
for pid, n in collections.Counter(c["pid"] for c in outn).items():
    ops = collections.Counter(c["op"] for c in cmds if c["pid"] == pid and c["off"] is None)
    print(f"      pid {pid}: {n} cmd(s), op counts {dict(ops)} (op 0=read, 1=write)")
print(f"\n  in-file large (>= {a.split_bytes} B) : {len(big):5d}   {sum(c['size'] for c in big)/2**20:8.2f} MiB")
print(f"  in-file small (<  {a.split_bytes} B) : {len(small):5d}   {sum(c['size'] for c in small)/1024:8.1f} KiB")
if small:
    print("\n  small-command sizes:", dict(sorted(collections.Counter(c["size"] for c in small).items())))
    fsz = max((c["off"] + c["size"]) for c in inn)
    # Only worth listing individually when they are the exception. A workload whose every command is
    # small (a KV read stream) would otherwise print thousands of uninformative lines.
    if len(small) <= 40:
        print("  small-command file offsets (KiB, % into file):")
        for c in sorted(small, key=lambda c: c["off"]):
            print(f"      {c['off']/1024:10.1f} KiB  {100.0*c['off']/fsz:6.2f}%  size={c['size']}")
    else:
        print(f"  ({len(small)} small commands, too many to list, see the size histogram above)")
