#!/usr/bin/env python3
"""Real end-to-end NIXL GDS workload for the cross-layer keystone. Drives the ACTUAL
nixl_agent + GDS backend (cuFileBatchIOSubmit under the hood) to read file->GPU (true P2P)
with a heterogeneous, NIXL-derived KV-cache access pattern:
  class A = large page reads  (default 1 MiB, 4K-aligned)   -> file region [0, BBASE)
  class B = small KV reads    (default 2560 B = Llama-3.1-70B PP=8 block-access)  -> [BBASE, .)
Transfers are issued in BATCHES (NIXL packs each initialize_xfer's descriptors into one
GDS batch), giving the real async/batched regime. Class is encoded by file region so the
tracer's LBA attribution (keystone_gds_score.py) can split per class.

Run under DATACRUMBS trace-all (no injection). Usage:
  nixl_gds_kv.py --file F [--na 200 --sa 1048576 --nb 2000 --sb 2560 --batch 64]
"""
import argparse, os
import cupy
try:
    from nixl_cu12._api import nixl_agent, nixl_agent_config   # match CUDA 12.6 driver
except ImportError:
    from nixl._api import nixl_agent, nixl_agent_config

BBASE = 1 << 30  # class B region starts at 1 GiB (matches keystone_gds_score.py)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="/mnt/nvme1/gdstrace-smoke/ovh.dat")
    ap.add_argument("--na", type=int, default=200); ap.add_argument("--sa", type=int, default=1 << 20)
    ap.add_argument("--nb", type=int, default=2000); ap.add_argument("--sb", type=int, default=2560)
    ap.add_argument("--batch", type=int, default=64)
    a = ap.parse_args()

    # interleaved op list (file_offset, size); scattered, non-overlapping per class
    ops = []
    ia = ib = 0
    ratio = max(1, a.nb // max(1, a.na))
    while ia < a.na or ib < a.nb:
        for _ in range(ratio):
            if ib < a.nb:
                ops.append((BBASE + ib * 65536 + 3072, a.sb)); ib += 1   # small KV, unaligned, scattered
        if ia < a.na:
            ops.append((ia * (4 << 20), a.sa)); ia += 1                  # large page, 4 MiB stride

    agent = nixl_agent("NIXLKV", nixl_agent_config(backends=[]))
    assert "GDS" in agent.get_plugin_list()
    agent.create_backend("GDS")

    # one big VRAM buffer; each op in a batch gets its own slot (stride by max size)
    slot = max(a.sa, a.sb)
    vbuf = cupy.zeros(a.batch * slot, dtype=cupy.uint8)
    vptr = int(vbuf.data.ptr)
    vram_reg = agent.register_memory([(vptr, a.batch * slot, 0, "")], "VRAM")
    assert vram_reg is not None

    fd = os.open(a.file, os.O_RDONLY | os.O_DIRECT)
    fsz = os.path.getsize(a.file)
    file_reg = agent.register_memory([(0, fsz, fd, "")], "FILE")
    assert file_reg is not None

    done_ops = 0
    for base in range(0, len(ops), a.batch):
        wave = ops[base:base + a.batch]
        gpu = [(vptr + j * slot, sz, 0) for j, (_, sz) in enumerate(wave)]
        fil = [(off, sz, fd) for (off, sz) in wave]
        gd = agent.get_xfer_descs(gpu, "VRAM")
        fdsc = agent.get_xfer_descs(fil, "FILE")
        h = agent.initialize_xfer("READ", gd, fdsc, "NIXLKV")
        assert h, "initialize_xfer failed"
        st = agent.transfer(h)
        assert st != "ERR", "transfer ERR"
        while True:
            st = agent.check_xfer_state(h)
            if st == "ERR": raise SystemExit("transfer ERR (batch)")
            if st == "DONE": break
        agent.release_xfer_handle(h)
        done_ops += len(wave)

    agent.deregister_memory(vram_reg); agent.deregister_memory(file_reg); os.close(fd)
    print(f"NIXL_GDS_KV ops={done_ops} (A={a.na}x{a.sa}B large, B={a.nb}x{a.sb}B KV) batch={a.batch}")

if __name__ == "__main__":
    main()
