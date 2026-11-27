# Per-op P2P attribution on the kernel PCI P2PDMA transport (2026-11-18)
Change: new kprobe on pci_p2pdma_map_segment in the nvidiafs plugin (event 400003), stamped with the active
cuFileRead corr_id like the nvidia-fs markers (23-datacrumbs.diff). trace_hdf5_gds.sh scores p2pdma_map and
accepts either marker as true GDS (23-trace_hdf5_gds.diff).

## HDF5, 64 MiB, 256 KiB chunks, P2PDMA mode, traced (22-hdf5-trace-p2pdma-3reps.log)
| case | cuFileRead | nvfs_io | p2pdma_map | nvme_setup_cmd | verdict | runs |
| cache ON | 262 | 0 | 0 | 284 | silent compat | 3/3 |
| cache OFF | 262 | 0 | 257 | 272 | true GDS (p2pdma) | 2/3 (rep 1 left no trace file) |
| contiguous | 8 | 0 | 72 | 81 | true GDS (p2pdma) | 3/3 |
Same verdicts as the nvidia-fs path (07-...): cache ON 262/0, cache OFF 262/256, contiguous 8/72.

## Per-op attribution (rep 3 traces)
cache OFF: 257/257 p2pdma events carry a corr_id matching a cuFileRead; 256 distinct reads covered, max 2 per read.
contiguous: 72/72 attributed, all 8 reads covered, 9 segments each.
cache ON: 0 events.

## Not yet run on P2PDMA
NIXL 4 KiB grid, unaligned writes, tail contention, command cap.

## Side check: NVIDIA gds-diag (MagnumIO d49288c), 20-gds-diag-all.{txt,json}
all /mnt/nvme1/gdstrace-smoke: completed, no FAIL. Warnings: MLNX_OFED/DOCA not installed (not needed for P2PDMA),
root fs not data=ordered (not the GDS mount). Every P2PDMA prerequisite PASS (kernel, registries, topology PIX,
ACS, cufile.json both keys). Its config-audit section reports the P2PDMA route as active (PASS, "NVMe P2PDMA: Supported").
Its mount-check section for this path lists the P2PDMA mode as INFO and notes "Direct GDS is still available via
nvidia-fs/nvfs". The two sections do not say which route a read on this mount takes; the counters and ftrace show
P2PDMA (nvidia-fs bypassed). gds-diag checks readiness and configuration, not what an application actually did.
Perf hint: nr_requests 1023 vs recommended 1024.
