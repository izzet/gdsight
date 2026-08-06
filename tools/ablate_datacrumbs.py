#!/usr/bin/env python3
"""A3 ablation: what does DEFAULT DataCrumbs see, versus DataCrumbs + our three plugins?

Derived by category-filtering traces we already have, so the comparison is within one run rather than
across two. The default substrate emits cat="custom1" (POSIX syscalls). Our plugins emit cat="cufile",
"nvidiafs" and "block". Filtering to custom1 reproduces exactly what a stock install would have captured.

Usage: ablate_datacrumbs.py <trace.pfw.gz> [...]   -> one summary line per trace
"""
import sys, gzip, json, collections

# ops a default install could see at the syscall layer
POSIX_IO = {"read", "pread64", "write", "pwrite64", "readv", "writev"}


def summarize(path):
    cat = collections.Counter()
    name = collections.Counter()
    with gzip.open(path, "rt", errors="ignore") as fh:
        for line in fh:
            line = line.strip().rstrip(",")
            if not line or line[0] != "{":
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            n, c = e.get("name"), e.get("cat", "")
            if not n:
                continue
            cat[c] += 1
            name[(c, n)] += 1

    posix_io = sum(v for (c, n), v in name.items() if c == "custom1" and n in POSIX_IO)
    cufile_ops = sum(v for (c, n), v in name.items()
                     if c == "cufile" and n in ("batchentry", "pread", "cuFileRead", "cuFileWrite"))
    nvme = name.get(("block", "nvme_setup_cmd"), 0)
    nvfs = name.get(("nvidiafs", "nvfs_io"), 0)
    p2p = name.get(("nvidiafs", "nvfs_get_p2p_dma_mapping"), 0)
    batch = name.get(("cufile", "cuFileBatchIOSubmit"), 0)
    return dict(trace=path.rsplit("/", 1)[-1][6:20], posix_io=posix_io, cufile_ops=cufile_ops,
                batch=batch, nvfs=nvfs, p2p=p2p, nvme=nvme,
                default_total=cat.get("custom1", 0), ours_total=sum(v for c, v in cat.items()
                                                                    if c in ("cufile", "nvidiafs", "block")))


if __name__ == "__main__":
    print(f"{'trace':16s}{'posixIO':>9s}{'cufileOps':>11s}{'batch':>7s}{'nvfs_io':>9s}{'p2p':>7s}{'nvme':>8s}"
          f"{'defaultEv':>11s}{'ourEv':>9s}")
    for p in sys.argv[1:]:
        try:
            s = summarize(p)
        except Exception as exc:
            print(f"{p.rsplit('/',1)[-1][6:20]:16s}  ERROR {exc}")
            continue
        print(f"{s['trace']:16s}{s['posix_io']:9d}{s['cufile_ops']:11d}{s['batch']:7d}{s['nvfs']:9d}"
              f"{s['p2p']:7d}{s['nvme']:8d}{s['default_total']:11d}{s['ours_total']:9d}")
