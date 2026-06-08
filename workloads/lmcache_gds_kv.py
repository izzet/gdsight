#!/usr/bin/env python
"""LMCache-style GDS KV-cache offload, driven via cufile-python (the exact module LMCache's GdsBackend
imports and calls in _save_gds / _load_gds). Replicates LMCache's GDS path: a 4 KiB POSIX metadata
header per chunk + a GPUDirect cuFileWrite of the KV tensor at offset 4096 (offload), then a cuFileRead
back into a GPU buffer (reload). Run under GDS-Trace to get per-op cuFile(write/read) <-> nvidia-fs <->
NVMe attribution for the inference-time KV-spill path.

NOTE: the full lmcache.v1.storage_backend.GdsBackend can't run here — lmcache 0.4.6 ships a CUDA-13
`c_ops` extension (needs libcudart.so.13) that won't load on this CUDA-12.6 node. cufile-python is
independent of c_ops, so this exercises the identical cuFile API LMCache uses. Usage:
  lmcache_gds_kv.py [--path DIR] [--nchunks N] [--chunk-mb M] [--reload]
"""
import argparse, ctypes, os, time
import torch
import cufile

_META = 4096  # LMCache _METADATA_MAX_SIZE: KV is GDS-written at this (page-aligned) file offset


def rw_counters():
    out = {}
    try:
        for ln in open("/proc/driver/nvidia-fs/stats"):
            if ln.startswith(("Reads", "Writes")):
                k = ln.split()[0].rstrip(":")
                out[k] = ln.strip()
    except OSError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/lmcache_kv")
    ap.add_argument("--nchunks", type=int, default=8)
    ap.add_argument("--chunk-mb", type=int, default=32)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--reload", action="store_true", help="also GDS-read the chunks back (KV reload)")
    args = ap.parse_args()
    os.makedirs(args.path, exist_ok=True)

    driver = cufile.CuFileDriver()  # cuFileDriverOpen, as GdsBackend does
    nbytes = args.chunk_mb * 1024 * 1024
    elems = nbytes // 2  # bf16
    b = rw_counters()

    # ---- offload: GDS-write each KV chunk (cuFileWrite) ----
    torch.cuda.synchronize(); t0 = time.time()
    files = []
    for i in range(args.nchunks):
        kv = torch.randn(elems, dtype=torch.bfloat16, device=args.device)
        addr = ctypes.c_void_p(kv.data_ptr())
        path = os.path.join(args.path, f"kvchunk_{i:04d}.bin")
        with open(path, "wb") as f:        # 4 KiB POSIX metadata header (as LMCache does)
            f.write(b"\0" * _META)
        with cufile.CuFile(path, "r+") as f:
            f.write(addr, nbytes, file_offset=_META, dev_offset=0)  # GDS write (offload)
        files.append(path)
    torch.cuda.synchronize(); wt = time.time() - t0
    tot = args.nchunks * nbytes / 2**30
    print(f"OFFLOAD (GDS write): {args.nchunks} chunks x {args.chunk_mb}MiB = {tot:.2f}GiB in {wt:.3f}s "
          f"({tot/wt:.2f}GiB/s)")

    # ---- reload: GDS-read each KV chunk back (cuFileRead) ----
    if args.reload:
        torch.cuda.synchronize(); t0 = time.time()
        for path in files:
            kv = torch.empty(elems, dtype=torch.bfloat16, device=args.device)
            addr = ctypes.c_void_p(kv.data_ptr())
            with cufile.CuFile(path, "r") as f:
                f.read(addr, nbytes, file_offset=_META, dev_offset=0)  # GDS read (reload)
        torch.cuda.synchronize(); rt = time.time() - t0
        print(f"RELOAD  (GDS read) : {tot:.2f}GiB in {rt:.3f}s ({tot/rt:.2f}GiB/s)")

    a = rw_counters()
    for k in ("Writes", "Reads"):
        print(f"  nvidia-fs {k} before: {b.get(k)}")
        print(f"  nvidia-fs {k} after : {a.get(k)}")


if __name__ == "__main__":
    main()
