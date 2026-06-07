#!/usr/bin/env python
"""step3_dali.py — Step 3 realism, 2nd vendor reader: NVIDIA DALI numpy GPU reader over the same
.npy dataset. device='gpu' + dont_use_mmap=True selects DALI's GDS path. Pair with a live gds_stats
attach to see whether DALI's reads use the GDS DMA path (n>0) or silently bypass it (n=0)."""
import sys, time
from nvidia.dali import pipeline_def, fn

DIR = sys.argv[1] if len(sys.argv) > 1 else "/mnt/nvme1/gdstrace-smoke/npy"
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

@pipeline_def(batch_size=1, num_threads=8, device_id=0)
def p():
    return fn.readers.numpy(device="gpu", file_root=DIR, file_filter="*.npy",
                            dont_use_mmap=True, random_shuffle=False, name="r")

pipe = p()
pipe.build()
import nvidia.dali as dali
print(f"DALI {dali.__version__} numpy GPU reader on {DIR}")
t0 = time.perf_counter(); it = 0
while time.perf_counter() - t0 < SECS:
    pipe.run(); it += 1
print(f"iters={it} time={time.perf_counter()-t0:.1f}s")
