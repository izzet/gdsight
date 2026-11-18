# Step 2: nvidia-fs baseline on CUDA 12.8 / GDS 1.13.1.3 / driver 570.211.01 (2026-11-18)
Node liqid05-grc (hostname izzet-gdstrace-node), A100-PCIE-40GB, nvme1n1 ext4 data=ordered, kernel 6.8.0-1051-nvidia.
libcufile confirmed at runtime: /usr/local/cuda-12.8/lib64/libcufile.so.0 (LD_DEBUG).

## Smoke (03-smoke-nvfs-cuda128.log), gdsio GPUD, 4 threads
read 1 MiB 2.894 GiB/s; randread 4K aligned 0.147, unaligned 0.115; 64K 0.693 / 0.637; 1M 2.923 / 2.724. gds_stats posix=0.
Matches the CUDA 12.6 run of 2026-11-17 within 2%.

## HDF5 A/B, nvidia-fs Reads counter, 64 MiB, 256 KiB chunks, 3 reps (04-hdf5-ab-nvfs-cuda128.log)
cache ON +0 | cache OFF +256 | contiguous +72, identical in all 3 reps.
BW cache ON 0.909/0.895/0.926, OFF 0.585/0.570/0.587, contiguous 1.598/1.636/1.621 GiB/s.

## HDF5 traced (07-hdf5-trace-nvfs-cuda128-fixed.log)
| case | cuFileRead | nvfs_io | p2p_map | nvme_setup_cmd |
| cache ON | 262 | 0 | 0 | 283 |
| cache OFF | 262 | 256 | 256 | 271 |
| contiguous | 8 | 72 | 72 | 81 |
CUDA 12.6 reference (2026-11-17): p2p_map 0/258/136, nvme_setup_cmd 288/276/148. Contiguous issues fewer P2P mappings and commands on 1.13; not yet explained.

## Issue found and fixed
cuFile uprobe path is hardcoded to CUDA 12.6 in external/datacrumbs/etc/datacrumbs/plugins/custom_probes/cufile/{probes.json,cufile.bpf.c}.
On 12.8 the first traced run (05-...log) showed cuFileRead=0 for all cases. Repointed to cuda-12.8 and rebuilt (06-...log).
