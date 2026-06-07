import os, numpy as np, cupy as cp, kvikio, kvikio.defaults as kd
print("kvikio", kvikio.__version__, "| cupy", cp.__version__, "| GPU:", cp.cuda.runtime.getDeviceProperties(0)['name'].decode())
# report kvikio's compat view (API name varies by version)
for attr in ("compat_mode","is_compat_mode_preferred","compat_mode_reset"):
    if hasattr(kd, attr):
        try: print(f"kvikio.defaults.{attr} ->", getattr(kd, attr)() if callable(getattr(kd,attr)) else getattr(kd,attr))
        except Exception as e: print(attr, "err", e)
path = "/mnt/nvme1/gdstrace-smoke/kvikio_test.bin"
n = 64*1024*1024                                  # 256 MiB of int32
a = (np.arange(n, dtype=np.int64) % 1000003).astype(np.int32)
a.tofile(path)
os.system("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null")
dev = cp.empty(n, dtype=cp.int32)
with kvikio.CuFile(path, "r") as f:
    got = f.read(dev)
ok = bool((cp.asnumpy(dev) == a).all())
print(f"bytes read via kvikio.CuFile: {got} ({got/2**20:.0f} MiB) | DATA CORRECT: {ok}")
print("RESULT:", "TRUE-GDS kvikio read OK" if ok else "MISMATCH")
