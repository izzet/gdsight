#!/usr/bin/env python
"""Load a safetensors shard via fastsafetensors (vLLM's --load-format fastsafetensors loader),
which uses GPUDirect Storage (cuFile) to DMA tensors NVMe->GPU. --nogds switches to the CPU-staged
fallback. Run under GDS-Trace (datacrumbs) to get per-op cuFile<->nvidia-fs<->NVMe attribution +
the GDS-vs-fallback cross-layer timing. Usage: fastsafetensors_load.py [--nogds] [--path P]"""
import argparse, glob, os, time
import torch
from fastsafetensors import SafeTensorsFileLoader, SingleGroup


def reads_counter():
    try:
        for ln in open("/proc/driver/nvidia-fs/stats"):
            if ln.startswith("Reads"):
                return ln.strip()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/model_shard.safetensors",
                    help="a .safetensors file OR a model dir (loads all *.safetensors shards, vLLM-style)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--nogds", action="store_true", help="CPU-staged fallback (no GPUDirect Storage)")
    args = ap.parse_args()

    if os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "*.safetensors")))
    else:
        files = [args.path]
    loader = SafeTensorsFileLoader(SingleGroup(), args.device, nogds=args.nogds, debug_log=False)
    loader.add_filenames({0: files})
    b = reads_counter()
    torch.cuda.synchronize()
    t0 = time.time()
    fb = loader.copy_files_to_device()
    torch.cuda.synchronize()
    dt = time.time() - t0
    a = reads_counter()
    sz = sum(os.path.getsize(f) for f in files) / 2**30
    mode = "nogds" if args.nogds else "GDS"
    print(f"mode={mode} load={dt:.3f}s files={len(files)} size={sz:.2f}GiB bw={sz/dt:.2f}GiB/s "
          f"tensors={len(loader.get_keys())}")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")
    loader.close()


if __name__ == "__main__":
    main()
