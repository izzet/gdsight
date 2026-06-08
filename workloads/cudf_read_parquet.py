#!/usr/bin/env python
"""Real GPU-analytics workload: RAPIDS cuDF read_parquet, which ingests Parquet column chunks directly
to GPU via cuFile/GDS (set LIBCUDF_CUFILE_POLICY=ALWAYS|GDS|KVIKIO). A Parquet file is row-groups x
column-chunks of wildly varying size (KB..MB) + per-column encodings, so cuDF issues a heterogeneous
mix of cuFile reads — exactly where per-op/per-column attribution could surface a non-obvious waste that
aggregate counters blend. Run under GDS-Trace. Usage: cudf_read_parquet.py [--path P] [--iters N]"""
import argparse, os, time


def reads_counter():
    for ln in open("/proc/driver/nvidia-fs/stats"):
        if ln.startswith("Reads") and "readMiB" in ln:
            return ln.strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/mnt/nvme1/gdstrace-smoke/parquet/yellow_2024-01.parquet")
    ap.add_argument("--iters", type=int, default=1)
    args = ap.parse_args()
    import cudf  # import after env (LIBCUDF_CUFILE_POLICY) is set by the caller
    print(f"cudf {cudf.__version__} | LIBCUDF_CUFILE_POLICY={os.environ.get('LIBCUDF_CUFILE_POLICY','<unset>')}")
    sz = os.path.getsize(args.path) / 2**20
    b = reads_counter()
    t0 = time.time()
    for _ in range(args.iters):
        df = cudf.read_parquet(args.path)
    dt = (time.time() - t0) / args.iters
    a = reads_counter()
    print(f"read_parquet {args.path.split('/')[-1]} ({sz:.1f} MiB file): {len(df)} rows x {df.shape[1]} cols "
          f"in {dt:.3f}s ({sz/dt:.0f} MiB/s)")
    print(f"  nvidia-fs Reads before: {b}")
    print(f"  nvidia-fs Reads after : {a}")


if __name__ == "__main__":
    main()
