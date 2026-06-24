#!/usr/bin/env python
"""Parametrized DeepNVMe GDS load (file->GPU via cuFile), exposing the five DeepNVMe tuning knobs so
GDS-Trace can attribute each knob's device-level effect (the "explain the autotuner" experiment). Mirrors
external/deepspeedexamples/deepnvme/file_access/gds_load_gpu_tensor.py but with block_size / queue_depth /
single_submit / overlap_events / intra_op_parallelism as CLI args.
Usage: deepnvme_gds_load.py --input_file F --block_size 1048576 --intra_op_parallelism 8 [--overlap_events]"""
import argparse, os, time
import torch
from deepspeed.ops.op_builder import GDSBuilder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_file", default="/mnt/nvme1/gdstrace-smoke/gds_test.bin")
    ap.add_argument("--block_size", type=int, default=1024 * 1024)
    ap.add_argument("--queue_depth", type=int, default=128)
    ap.add_argument("--single_submit", action="store_true")
    ap.add_argument("--overlap_events", action="store_true")
    ap.add_argument("--intra_op_parallelism", type=int, default=1)
    ap.add_argument("--loop", type=int, default=1)
    args = ap.parse_args()

    file_sz = os.path.getsize(args.input_file)
    h = GDSBuilder().load().gds_handle(args.block_size, args.queue_depth, args.single_submit,
                                       args.overlap_events, args.intra_op_parallelism)
    buf = h.new_pinned_device_tensor(file_sz, torch.empty(0, dtype=torch.uint8, device="cuda",
                                                          requires_grad=False))
    t0 = time.time()
    for _ in range(args.loop):
        h.sync_pread(buf, args.input_file)
    dt = (time.time() - t0) / args.loop
    h.free_pinned_device_tensor(buf)
    print(f"DEEPNVME-GDS: {file_sz/2**30:.2f}GB block={args.block_size} qd={args.queue_depth} "
          f"single={args.single_submit} overlap={args.overlap_events} parallel={args.intra_op_parallelism} "
          f"-> {dt:.3f}s {file_sz/2**30/dt:.2f} GB/s (expect ~{file_sz//args.block_size} device cmds)")


if __name__ == "__main__":
    main()
