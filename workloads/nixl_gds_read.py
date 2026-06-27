#!/usr/bin/env python
"""Real transfer engine: NVIDIA NIXL (the data-mover under Dynamo disaggregated inference) reading a file
into GPU memory via its GPUDirect Storage backend (cuFile, VRAM). NIXL transfers are async (post +
check_xfer_state), so with --inflight>1 many transfers overlap on the device — the regime where GDSight's
LBA matcher attributes and the timing/thread heuristic degrades. Run under GDSight to attribute the real
engine's device traffic per logical transfer. GPU buffers via ctypes cudaMalloc (no torch). Usage:
--nreads 2000 --inflight 32"""
import argparse, ctypes, os, random, time
try:
    from nixl_cu12._api import nixl_agent, nixl_agent_config   # cu12 wheel (matches our 12.6 driver)
except ImportError:
    from nixl._api import nixl_agent, nixl_agent_config

CUDART = "/usr/local/cuda-12.6/targets/x86_64-linux/lib/libcudart.so"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/dataset.bin")
    ap.add_argument("--nreads", type=int, default=2000)
    ap.add_argument("--inflight", type=int, default=32)
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    random.seed(args.seed)

    cudart = ctypes.CDLL(CUDART)
    cudart.cudaSetDevice(0)
    cudart.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]

    def cuda_malloc(n):
        p = ctypes.c_void_p()
        rc = cudart.cudaMalloc(ctypes.byref(p), ctypes.c_size_t(n))
        assert rc == 0, f"cudaMalloc rc={rc}"
        return p.value

    agent = nixl_agent("gdsreader", nixl_agent_config(backends=[]))
    agent.create_backend("GDS")

    ptrs = [cuda_malloc(args.size) for _ in range(args.inflight)]
    reg = agent.get_reg_descs([(p, args.size, 0, "") for p in ptrs], "VRAM")
    assert agent.register_memory(reg) is not None

    fd = os.open(args.path, os.O_RDONLY)
    fsz = os.path.getsize(args.path)
    file_reg = agent.register_memory([(0, fsz, fd, "")], "FILE")
    assert file_reg is not None
    nslots = fsz // args.size

    t0 = time.time()
    done = 0
    while done < args.nreads:
        k = min(args.inflight, args.nreads - done)
        handles = []
        for j in range(k):
            off = random.randrange(nslots) * args.size
            vram = agent.get_xfer_descs([(ptrs[j], args.size, 0)], "VRAM")
            fdsc = agent.get_xfer_descs([(off, args.size, fd)], "FILE")
            h = agent.initialize_xfer("READ", vram, fdsc, "gdsreader")
            if not h:
                raise RuntimeError("initialize_xfer failed")
            agent.transfer(h)
            handles.append(h)
        for h in handles:
            while agent.check_xfer_state(h) not in ("DONE", "ERR"):
                pass
            agent.release_xfer_handle(h)
        done += k
    dt = time.time() - t0

    agent.deregister_memory(reg)
    agent.deregister_memory(file_reg)
    os.close(fd)
    print(f"NIXL-GDS: {args.nreads} reads ({args.size}B, {args.inflight} in flight) in {dt:.3f}s "
          f"({args.nreads*args.size/2**20/dt:.0f} MiB/s)")


if __name__ == "__main__":
    main()
