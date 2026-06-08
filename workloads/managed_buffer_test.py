#!/usr/bin/env python
"""Edge case: does cuFile silently bounce a managed/unified-memory buffer? Reads a file into either a
cudaMalloc (device) buffer or a cudaMallocManaged (unified) buffer via cuFile, and reports the kernel
nvidia-fs counters. Run under bpftrace on nvfs_get_p2p_dma_mapping (true P2P) vs
nvfs_mgroup_pin_shadow_pages (host bounce) to see the per-op path. Usage: --kind device|managed"""
import argparse, ctypes, os

CUDART = "/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcudart.so"


def reads_counter():
    for ln in open("/proc/driver/nvidia-fs/stats"):
        if ln.startswith("Reads") and "readMiB" in ln:
            return ln.strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["device", "managed"], default="device")
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--size", type=int, default=64 * 1024 * 1024)
    args = ap.parse_args()

    cudart = ctypes.CDLL(CUDART)
    cudart.cudaSetDevice(0)
    p = ctypes.c_void_p()
    if args.kind == "device":
        cudart.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        rc = cudart.cudaMalloc(ctypes.byref(p), ctypes.c_size_t(args.size))
    else:
        cudart.cudaMallocManaged.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t, ctypes.c_uint]
        rc = cudart.cudaMallocManaged(ctypes.byref(p), ctypes.c_size_t(args.size), ctypes.c_uint(1))
    assert rc == 0, f"alloc rc={rc}"
    cudart.cudaDeviceSynchronize()

    import cufile
    drv = cufile.CuFileDriver()
    b = reads_counter()
    try:
        with cufile.CuFile(args.path, "r") as f:
            nread = f.read(ctypes.c_void_p(p.value), args.size, file_offset=0, dev_offset=0)
        err = None
    except Exception as e:
        nread, err = -1, repr(e)
    cudart.cudaDeviceSynchronize()
    a = reads_counter()
    print(f"kind={args.kind} requested={args.size//2**20}MiB read={nread} err={err}")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")


if __name__ == "__main__":
    main()
