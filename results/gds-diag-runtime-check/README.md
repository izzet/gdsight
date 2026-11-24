# gds-diag `runtime-check`: validation

Run on 2026-11-24 on a Chameleon CHI@TACC `compute_liqid` node (`liqid07-grc`): A100-PCIE-40GB, Samsung
MZ1LB3T8HMLA NVMe (`nvme1n1`, ext4 `data=ordered`), CUDA 12.6, GDS 1.11.1, nvidia-fs 2.28.4, the
nvidia-fs route active and P2PDMA not configured. `runtime-check` is the prototype in
`external/nvidia-magnum-io` (`izzet/nvidia-magnum-io`, branch `feat/gdsight-runtime-check`, commit `ce43d68`).

Five workloads, each run as a user (`plain`) and as root with `--kernel-trace` (`ktrace`), with the page cache
dropped before every run (`run_runtime_check.sh`). HDF5: 64 MiB, 256 KiB chunks. `gdsio`: 1 GiB in 1 MiB reads.

| Workload | nvidia-fs reads | `nvfs_get_p2p_dma_mapping` | `filemap_read` | Device MiB | Verdict |
|---|--:|--:|--:|--:|---|
| HDF5, chunk cache on | 0 | 0 | 296 | 64.1 | WARN: host-staged although the nvidia-fs route is active |
| HDF5, chunk cache off | 256 | 256 | 40 | 65.1 | PASS: direct GDS |
| HDF5, contiguous | 72 | 72 | 34 | 64.1 | PASS: direct GDS |
| `gdsio -x 0` (GPU direct) | 1024 | 1024 | 34 | 1024.0 | PASS: direct GDS |
| `gdsio -x 1` (CPU only) | 0 | 0 | 19 | 1024.0 | WARN: host-staged although the nvidia-fs route is active |

Kernel-function columns come from the `ktrace` runs; the `plain` runs reach the same verdicts from the
nvidia-fs counter alone (`summary.txt`). The direct-read counts equal the `nvfs_io` counts GDSight attributes
per operation for the same workloads (`results/xlayer/hdf5-gds.md`).

`gds-diag mount-check` passes this path. Only the runtime check shows that the HDF5 run with the chunk cache
never used the direct route, and, because the route is active, that the cause is the application.

Not covered: the PCI P2PDMA branch (`pci_p2pdma_map_segment`), which the node was not configured for; it is
covered by unit tests in the prototype and by the measurements in `results/p2pdma/`.

Files: `rc_<workload>_<plain|ktrace>.json` (`runtime-check --json` output), `summary.txt`, `rc_summary.py`,
`run_runtime_check.sh`.
