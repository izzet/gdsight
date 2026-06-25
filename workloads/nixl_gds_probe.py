#!/usr/bin/env python3
"""Minimal real-NIXL GDS gate test: drive the actual nixl_agent + GDS backend to do a
TRUE P2P read (file -> GPU VRAM) on this node's drive. Confirms NIXL's GDS backend
(cuFileBatchIOSubmit under the hood) actually executes here before we build the full
KV workload. Usage: nixl_gds_probe.py <file_on_GDS_mount>"""
import os, sys
import cupy
# Use the CUDA-12 NIXL build (matches this node's 12.6 driver; the cu13 wheel mismatches).
try:
    from nixl_cu12._api import nixl_agent, nixl_agent_config
except ImportError:
    from nixl._api import nixl_agent, nixl_agent_config

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/nvme1/gdstrace-smoke/ovh.dat"
    size = 1 << 20  # 1 MiB true-GDS read into VRAM
    agent = nixl_agent("GDSProbe", nixl_agent_config(backends=[]))
    assert "GDS" in agent.get_plugin_list(), "GDS plugin missing"
    agent.create_backend("GDS")

    # GPU (VRAM) destination buffer
    buf = cupy.zeros(size, dtype=cupy.uint8)
    dptr = int(buf.data.ptr)
    vram_reg = agent.register_memory([(dptr, size, 0, "")], "VRAM")
    assert vram_reg is not None, "VRAM register failed"
    vram_xfer = agent.get_xfer_descs([(dptr, size, 0)], "VRAM")

    fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
    file_reg = agent.register_memory([(0, size, fd, "")], "FILE")
    assert file_reg is not None, "FILE register failed"
    file_xfer = file_reg.trim()

    h = agent.initialize_xfer("READ", vram_xfer, file_xfer, "GDSProbe")
    assert h, "initialize_xfer failed"
    st = agent.transfer(h)
    assert st != "ERR", "transfer ERR at submit"
    while True:
        st = agent.check_xfer_state(h)
        if st == "ERR":
            print("RESULT: ERR (GDS batch transfer failed on this drive)"); sys.exit(1)
        if st == "DONE":
            break
    head = bytes(cupy.asnumpy(buf[:16]))   # D2H copy only (no compute kernel)
    print(f"RESULT: OK — NIXL GDS file->VRAM read of {size} B completed (first16={head.hex()})")
    agent.release_xfer_handle(h)
    agent.deregister_memory(vram_reg); agent.deregister_memory(file_reg)
    os.close(fd)

if __name__ == "__main__":
    main()
