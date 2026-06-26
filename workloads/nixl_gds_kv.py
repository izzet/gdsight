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
import argparse, os, time
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
    # KV-cache LAYOUT (the optimization lever, O1/O2). Class-B reads only:
    #   scatter = scattered + sub-4K-misaligned (naive paged KV cache)        -> high amplification
    #   aligned = 4K-aligned but still one read per entry (the "obvious" fix)  -> partial
    #   packed  = entries laid out contiguously + bulk-read in 1 MiB chunks    -> coalesced, ~1.0x
    ap.add_argument("--layout", default="scatter", choices=["scatter", "aligned", "packed"])
    ap.add_argument("--stride", type=int, default=65536)
    a = ap.parse_args()

    # class-B (KV) ops by layout
    if a.layout == "packed":
        total = a.nb * a.sb; chunk = 1 << 20
        bops = [(BBASE + j, min(chunk, total - j)) for j in range(0, total, chunk)]
    else:
        mis = 3072 if a.layout == "scatter" else 0   # aligned: 4K-aligned offsets
        bops = [(BBASE + i * a.stride + mis, a.sb) for i in range(a.nb)]
    aops = [(i * (4 << 20), a.sa) for i in range(a.na)]   # class-A large pages, 4 MiB stride
    # interleave (ratio B per A) so a batch mixes classes; pure-B when na=0
    ops = []
    ia = ib = 0
    ratio = max(1, len(bops) // max(1, len(aops)))
    while ia < len(aops) or ib < len(bops):
        for _ in range(ratio):
            if ib < len(bops):
                ops.append(bops[ib]); ib += 1
        if ia < len(aops):
            ops.append(aops[ia]); ia += 1
    useful = a.nb * a.sb + a.na * a.sa   # application-requested bytes (same across layouts)

    agent = nixl_agent("NIXLKV", nixl_agent_config(backends=[]))
    assert "GDS" in agent.get_plugin_list()
    agent.create_backend("GDS")

    # one big VRAM buffer; each op in a batch gets its own slot (stride by max op size)
    slot = max(sz for _, sz in ops)
    vbuf = cupy.zeros(a.batch * slot, dtype=cupy.uint8)
    vptr = int(vbuf.data.ptr)
    vram_reg = agent.register_memory([(vptr, a.batch * slot, 0, "")], "VRAM")
    assert vram_reg is not None

    fd = os.open(a.file, os.O_RDONLY | os.O_DIRECT)
    fsz = os.path.getsize(a.file)
    file_reg = agent.register_memory([(0, fsz, fd, "")], "FILE")
    assert file_reg is not None

    done_ops = 0
    t0 = time.monotonic()
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
    elapsed = time.monotonic() - t0

    agent.deregister_memory(vram_reg); agent.deregister_memory(file_reg); os.close(fd)
    print(f"NIXL_GDS_KV ops={done_ops} layout={a.layout} (A={a.na}x{a.sa}B, B={a.nb}x{a.sb}B KV) batch={a.batch} "
          f"useful={useful/2**20:.1f}MiB elapsed={elapsed*1000:.1f}ms useful_GiBps={useful/elapsed/2**30:.3f}")

if __name__ == "__main__":
    main()
