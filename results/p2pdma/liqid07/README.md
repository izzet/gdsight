# HDF5 on the kernel PCI P2PDMA transport, both VFD forks (liqid07)

Run on 2026-11-27 on Chameleon `liqid07-grc`: A100-PCIE-40GB, Samsung MZ1LB3T8HMLA NVMe (`nvme1n1`, ext4
`data=ordered`), kernel `6.8.0-1051-nvidia`, IOMMU off. The node moved from the paper's stack (driver 560.35.05,
CUDA 12.6, cuFile 1.11.1.6) to driver 570.211.01, CUDA 12.8.2, cuFile 1.13.1.3, nvidia-fs 2.28.4, the same
versions as the liqid05 run in [`../README.md`](../README.md).

The same workload and script run twice: on the nvidia-fs transport with cuFile 1.13 (`03-…`), then on the
P2PDMA transport (`06-…`). The first run separates the cuFile version from the transport.

Workload: `workloads/hdf5_gds.c`, HDF5 1.14.5, 64 MiB dataset, 256 KiB chunks, cold page cache, three reps per
case. VFDs: `hpc-io/vfd-gds` 1.0.3 (the default) and `nv-legate/vfd-gds`, both built against CUDA 12.8
(`VFD_FORK=… CUDA=/usr/local/cuda-12.8 bash chameleon/build_hdf5_gds.sh`, `02-…`). Script:
[`h5_transport_counts.sh`](h5_transport_counts.sh).

## Results

All three reps gave identical counts. Kernel counts are ftrace function-profiler hits.

| | nvidia-fs, cuFile 1.13 | P2PDMA, cuFile 1.13 |
|---|---|---|
| **hpc-io** `pread64` (strace -f -c), cache on / off / contiguous | 272 / 16 / 10 | 272 / 273 / 79 |
| **nv-legate** `pread64`, cache on / off / contiguous | 264 / 8 / 2 | 264 / 265 / 75 |
| hpc-io P2P mappings (`nvfs_get_p2p_dma_mapping` / `pci_p2pdma_map_segment`) | 0 / 257 / 100–132 | 0 / 257 / 68 |
| nv-legate P2P mappings | 0 / 257 / 72 | 0 / 257 / 72 |
| nvidia-fs `Reads n=`, hpc-io / nv-legate | 0 / 256 / 68 and 0 / 256 / 72 | 0 in every case |
| hpc-io GiB/s, cache on / off / contiguous | 1.70–1.72 / 0.78 / 2.48–2.50 | 1.73–1.74 / 0.79–0.80 / 2.62–2.67 |
| nv-legate GiB/s | 0.87–0.89 / 0.57–0.58 / 1.57–1.59 | 0.90–0.91 / 0.60–0.61 / 1.66–1.68 |

The first hpc-io cache-on rep on P2PDMA ran at 0.85 GiB/s; reps 2 and 3 at 1.73–1.74.

1. **The cuFile version does not change the syscall pattern.** On nvidia-fs, cuFile 1.13 gives the same
   `pread64` counts as cuFile 1.11 on this node (hpc-io 272 / 16 / 10, nv-legate 264 / 8 / 2;
   [`../../xlayer/hdf5-gds-hpc-io.md`](../../xlayer/hdf5-gds-hpc-io.md)).
2. **On P2PDMA a plain `pread64` count no longer separates the cases.** The direct cases gain one `pread64`
   per P2P read: cache off +257 on both forks, contiguous +69 (hpc-io) and +73 (nv-legate). On nvidia-fs the
   direct read is an ioctl to nvidia-fs; on P2PDMA cuFile issues it as an `O_DIRECT` `pread64` into GPU memory,
   which the kernel maps through `pci_p2pdma_map_segment` (`07-…`: in a cache-off run under strace, 255 of the
   273 calls are 266,240-byte reads on cuFile's own descriptor of `chunk.h5`, and the run still made 257 P2P
   mappings).
3. **The destination buffer still separates them.** strace prints the buffer of a `pread64` into host memory
   and shows only the address of one into GPU memory. Counting `pread64` calls on the HDF5 file by
   destination (`08-…`, one strace file per thread):

   | | into GPU memory | into host memory |
   |---|--:|--:|
   | hpc-io, cache on / off / contiguous | 0 / 256 / 68 | 270 / 15 / 9 |
   | nv-legate, cache on / off / contiguous | 0 / 256 / 72 | 262 / 7 / 1 |

   The GPU-memory counts match the P2P mapping counts, and the host-memory counts follow the same pattern as
   on nvidia-fs.
4. **The host-staging finding holds on both forks and both transports:** with the chunk cache on, there are
   no P2P mappings and every chunk read lands in host memory.

## Notes

- hpc-io's contiguous read splits across four cuFile threads. On nvidia-fs its P2P mapping count varied
  (100, 116, 132) with 68 nvidia-fs reads; on P2PDMA it was 68 in every rep.
- `/etc/cufile.json` on this image is a symlink into the CUDA tree. The first `enable_p2pdma.sh` run backed up
  the symlink instead of the file; the backup was replaced with the packaged `libcufile-12-8` file
  (`04-cufile.json.before-p2pdma`), and the script now copies the file.
- `/mnt/nvme1` is not in `/etc/fstab`; it was remounted `-o data=ordered` after each reboot.
- The paper's tracer install (`~/dc-prefix`) probes the CUDA 12.6 libcufile and was not rebuilt; GDSight
  traces were not taken in this run.

## Files

- `00-…`: state before the upgrade; `00-cufile.json.pre-cuda126` is the CUDA 12.6 cufile.json.
- `01-apt-install.log`: `apt-get install nvidia-driver-570-open cuda-toolkit-12-8 nvidia-gds-12-8`.
- `02-…`: the two VFD builds against CUDA 12.8.
- `03-nvfs-cuda128/`: nvidia-fs transport, cuFile 1.13 (`counters.csv`, `pread64.csv`).
- `04-…`, `05-…`: P2PDMA enablement, cufile.json before and after, `gdscheck` showing `NVMe P2PDMA : Supported`.
- `06-p2pdma-cuda128/`: P2PDMA transport, same measurements.
- `07-…`: full strace of one hpc-io cache-off run on P2PDMA.
- `08-…`: `pread64` calls by destination buffer on P2PDMA, and the per-case strace output.
