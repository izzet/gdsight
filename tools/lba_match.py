#!/usr/bin/env python
"""Deterministic cross-layer attribution by device address (LBA).

The corr_id heuristic (per-tid + tgid-count fallback) attributes NVMe commands to the causing cuFileRead
by *thread/timing*, which degrades under cuFile worker-thread decoupling + async concurrency. This does it
by *address* instead: map each cuFileRead's file range -> device LBAs via the file extent map (FIEMAP /
filefrag), then match every NVMe command's sector back to the cuFileRead that requested that file range.
Address-based correlation is independent of which thread ran the op or when, so it stays correct under
arbitrary concurrency. We then compare LBA-attribution vs corr_id-attribution on the same trace.

Usage: lba_match.py --trace T.pfw.gz --file /mnt/nvme1/gdstrace-smoke/dataset.bin
"""
import argparse, bisect, gzip, json, re, subprocess

EXT_RE = re.compile(r'^\s*\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+)\.\.\s+\d+:\s+(\d+):')


def extents(path):
    """sorted list of (phys_start_block, logical_start_block, length_blocks) from filefrag -v."""
    out = subprocess.run(["filefrag", "-v", path], capture_output=True, text=True).stdout
    exts = []
    for ln in out.splitlines():
        m = EXT_RE.match(ln)
        if m:
            exts.append((int(m.group(2)), int(m.group(1)), int(m.group(3))))  # (phys, logical, len)
    exts.sort()
    return exts


def phys_to_logical(exts, phys_starts, phys_block):
    i = bisect.bisect_right(phys_starts, phys_block) - 1
    if i < 0:
        return None
    phys_s, log_s, length = exts[i]
    return log_s + (phys_block - phys_s) if phys_block < phys_s + length else None


def parse_trace(path):
    cufile, nvme = [], []
    with gzip.open(path, "rt", errors="replace") as f:
        for ln in f:
            ln = ln.strip().rstrip(",")
            if not ln or ln[0] != "{":
                continue
            try:
                e = json.loads(ln)
            except Exception:
                continue
            n, a = e.get("name"), e.get("args", {})
            if n == "cuFileRead" and "offset" in a:
                cufile.append((int(a.get("corr_id", -1)), int(a["offset"]), int(a["size"])))
            elif n == "nvme_setup_cmd" and "sector" in a:
                nvme.append((int(a.get("corr_id", -1)), int(a["sector"]), int(a.get("size", 0))))
    return cufile, nvme


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--file", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--bs", type=int, default=4096, help="fs block size")
    ap.add_argument("--sec", type=int, default=512, help="device sector size")
    args = ap.parse_args()

    exts = extents(args.file)
    phys_starts = [e[0] for e in exts]
    cufile, nvme = parse_trace(args.trace)
    corr_ids = {c[0] for c in cufile if c[0] > 0}
    # cuFileRead intervals sorted by file offset, for range-contains lookup
    reads = sorted(cufile, key=lambda c: c[1])
    starts = [c[1] for c in reads]
    maxsz = max((c[2] for c in reads), default=0)

    def covering(file_off):
        """cuFileReads whose [offset, offset+size) contains file_off."""
        hits = []
        i = bisect.bisect_right(starts, file_off)
        j = i - 1
        while j >= 0 and starts[j] > file_off - maxsz:
            cid, off, sz = reads[j]
            if off <= file_off < off + sz:
                hits.append(cid)
            j -= 1
        return hits

    n = len(nvme)
    lba_unique = lba_ambig = lba_unmapped = corrid_ok = agree = 0
    for cid, sector, _ in nvme:
        phys_block = (sector * args.sec) // args.bs
        logical = phys_to_logical(exts, phys_starts, phys_block)
        if logical is None:
            lba_unmapped += 1
            lba_cid = None
        else:
            file_off = logical * args.bs + (sector * args.sec) % args.bs
            hits = covering(file_off)
            if len(hits) == 1:
                lba_unique += 1; lba_cid = hits[0]
            elif len(hits) > 1:
                lba_ambig += 1; lba_cid = None  # same file range read by >1 op: address can't disambiguate
            else:
                lba_unmapped += 1; lba_cid = None
        if cid in corr_ids:
            corrid_ok += 1
            if lba_cid is not None and lba_cid == cid:
                agree += 1

    pct = lambda x: f"{100*x/n:.1f}%" if n else "n/a"
    print(f"trace: {args.trace.split('/')[-1]}")
    print(f"file extents: {len(exts)} | cuFileReads: {len(cufile)} | NVMe cmds: {n}")
    print(f"  corr_id   attributed : {corrid_ok:5d}/{n}  ({pct(corrid_ok)})   <- thread/timing heuristic")
    print(f"  LBA       attributed : {lba_unique:5d}/{n}  ({pct(lba_unique)})   <- deterministic by address")
    print(f"     (LBA ambiguous: {lba_ambig} same-range reads; unmapped: {lba_unmapped})")
    if corrid_ok:
        print(f"  agreement (LBA vs corr_id, where corr_id assigned): {agree}/{corrid_ok} ({100*agree/corrid_ok:.1f}%)")


if __name__ == "__main__":
    main()
