# Where the extra device commands come from, 2026-08-07

Both gaps the paper reports (HDF5 256 vs 272, NIXL 2000 vs 2003) are accounted for here with
tools/classify_commands.py. They have DIFFERENT causes, which is why one number is stable and the
other is not.

## HDF5 true-GDS run (chunked, cache OFF)
`/tmp/hdf5_gds read /mnt/nvme1/gdstrace-smoke/h5_chunk.h5 off` under datacrumbs_run
```
trace : /mnt/nvme1/gdstrace-smoke/dc-traces/26/08/07/trace-cc-20260807200900-izzet-gdstrace-node-izzet-gdstrace-node.pfw.gz
file  : /mnt/nvme1/gdstrace-smoke/h5_chunk.h5

upper layers: cuFileRead=262  nvfs_get_p2p_dma_mapping=259  nvfs_io=256  pread=116

nvme_setup_cmd total : 274
  in this file's extents  : 274
  NOT in this file        : 0   <- not the workload's

  in-file large (>= 200000 B) :   256      64.96 MiB
  in-file small (<  200000 B) :    18      116.0 KiB

  small-command sizes: {4096: 12, 8192: 3, 12288: 1, 16384: 2}
  small-command file offsets (KiB, % into file):
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=4096
             0.0 KiB    0.00%  size=16384
         16640.0 KiB   25.38%  size=4096
         16644.0 KiB   25.39%  size=4096
         24576.0 KiB   37.49%  size=8192
         31236.0 KiB   47.65%  size=8192
         40960.0 KiB   62.48%  size=12288
         45832.0 KiB   69.92%  size=4096
         60424.0 KiB   92.18%  size=8192
         65536.0 KiB   99.98%  size=16384
```

## NIXL KV batch run (2000 x 2560 B, batch=64)
the regression arm of tools/run_candidate_lifetime.sh
```
trace : /mnt/nvme1/gdstrace-smoke/dc-traces/26/08/07/trace-cc-20260807190032-izzet-gdstrace-node-izzet-gdstrace-node.pfw.gz
file  : /mnt/nvme1/gdstrace-smoke/ovh.dat

upper layers: batchentry=2000  nvfs_get_p2p_dma_mapping=2000  nvfs_io=2000  pread=225

nvme_setup_cmd total : 2003
  in this file's extents  : 2000
  NOT in this file        : 3   <- not the workload's
      pid 1969: 1 cmd(s), op counts {0: 1} (op 0=read, 1=write)
      pid 13902: 2 cmd(s), op counts {1: 2} (op 0=read, 1=write)

  in-file large (>= 200000 B) :     0       0.00 MiB
  in-file small (<  200000 B) :  2000    16000.0 KiB

  small-command sizes: {8192: 2000}
  (2000 small commands, too many to list, see the size histogram above)
```

## Reading

**HDF5, 256 vs 272 (274 here).** All commands are inside the file, so nothing foreign is involved.
Exactly **256 large commands totalling 64.96 MiB pair 1:1 with the 256 nvfs_io P2P ops** for the
64 MiB dataset. The remainder are **18 small reads totalling 116 KiB**, which is HDF5's own metadata:
ten at file offset 0 (superblock and root object header, re-read as the library walks it) and eight
scattered 4 to 16 KiB reads at chunk-index B-tree nodes, including the 16 KiB one at 99.98% where the
chunk index sits. None carries a P2P mapping, which is why they are absent from nvfs_io. The count
varies run to run (16 in the committed hdf5_gds_trace.csv, 18 here) because how many metadata reads
reach the device depends on cache state, so it should not be quoted as an exact figure.

**NIXL, 2000 vs 2003.** Different cause entirely. The 3 extra commands are **not the workload's**:
two ext4 journal writes from `jbd2/nvme1n1-8` and one block-layer readahead from
`kworker/65:1H-kblockd`, all at physical blocks outside the file's extent map, and the two writes
appear in a workload that only reads. The address basis declines to attribute them to any operation,
which is the intended behaviour and a property an aggregate counter cannot have: iostat necessarily
folds this traffic into the workload's account. The namespace is shared with other tenants, so this
floor is a permanent feature of the setup, not noise to be averaged away.

Incidentally the NIXL histogram is itself the amplification result: every one of the 2000 commands is
8192 B for a 2560 B request, which is the 3.20x this arm measures at SB=2560 (4.00x at SB=2048).
