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
    # --- diagnosis knobs (the fixes our attribution prescribes) ---
    # kv-align: byte offset of each KV read within its 64K slot. 3072 = sub-4K misaligned (baseline,
    #   spans 2 blocks -> 3.2x); 0 = 4K-aligned (1 block per 2560B -> 1.6x).
    ap.add_argument("--kv-align", type=int, default=3072)
    # kv-coalesce: if >0, read the KV class as contiguous 4K-aligned chunks of this size instead of
    #   scattered sb reads (models storing KV contiguously) -> A_byte 1.0x. Same total useful bytes.
    ap.add_argument("--kv-coalesce", type=int, default=0)
    a = ap.parse_args()

    # interleaved op list (file_offset, size); scattered, non-overlapping per class
    ops = []
    ia = ib = 0
    if a.kv_coalesce > 0:
        total_b = a.nb * a.sb
        chunk = a.kv_coalesce
        nchunks = (total_b + chunk - 1) // chunk
        b_ops = [(BBASE + k * chunk, chunk) for k in range(nchunks)]   # contiguous, 4K-aligned
        nb_eff = nchunks; ratio = max(1, nb_eff // max(1, a.na))
        kb = 0
        while ia < a.na or kb < nb_eff:
            for _ in range(ratio):
                if kb < nb_eff: ops.append(b_ops[kb]); kb += 1
            if ia < a.na: ops.append((ia * (4 << 20), a.sa)); ia += 1
    else:
        ratio = max(1, a.nb // max(1, a.na))
        while ia < a.na or ib < a.nb:
            for _ in range(ratio):
                if ib < a.nb:
                    ops.append((BBASE + ib * 65536 + a.kv_align, a.sb)); ib += 1  # KV read, alignment=knob
            if ia < a.na:
                ops.append((ia * (4 << 20), a.sa)); ia += 1                  # large page, 4 MiB stride

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

    done_ops = 0; useful = sum(sz for _, sz in ops)
    t0 = time.perf_counter()
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
    dt = time.perf_counter() - t0

    agent.deregister_memory(vram_reg); agent.deregister_memory(file_reg); os.close(fd)
    gp = useful / dt / (1 << 20)
    print(f"NIXL_GDS_KV ops={done_ops} (A={a.na}x{a.sa}B large, B={a.nb}x{a.sb}B KV) "
          f"batch={a.batch} align={a.kv_align} coalesce={a.kv_coalesce} | "
          f"useful={useful/(1<<20):.2f}MiB elapsed={dt*1e3:.1f}ms goodput={gp:.1f}MiB/s")

if __name__ == "__main__":
    main()
