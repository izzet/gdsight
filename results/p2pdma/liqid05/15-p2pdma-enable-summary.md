# Enabling the kernel PCI P2PDMA transport (2026-11-18)
Stack: CUDA 12.8.2, GDS/libcufile 1.13.1.3, open driver 570.211.01, kernel 6.8.0-1051-nvidia (CONFIG_PCI_P2PDMA=y), A100-PCIE-40GB.

## Setting use_pci_p2pdma alone is not enough
properties.use_pci_p2pdma=true -> gdscheck still "NVMe P2PDMA : Unsupported" (10-gdscheck-p2pdma-on.txt).

## What it took (NVIDIA GDS troubleshooting guide v1.20, section 14)
1. /etc/cufile.json: properties.use_pci_p2pdma=true AND block.nvme.use_pci_p2pdma=true (block section added).
2. /etc/modprobe.d/nvidia-p2pdma.conf: options nvidia NVreg_RegistryDwords="RMForceStaticBar1=1;ForceP2P=0;RmForceDisableIomapWC=1;"  (ForceP2P=0 is for pre-Hopper GPUs such as A100)
3. /etc/modprobe.d/nvme.conf: options nvme_core multipath=N   (was Y)
4. update-initramfs -u, reboot.
Result: gdscheck "NVMe P2PDMA : Supported" (12-gdscheck-after-registry.txt). IOMMU still disabled (amd_iommu=off).

## Evidence the I/O uses P2PDMA (13-gdsio-p2pdma.txt, 14-gds-stats-p2pdma.txt)
nvidia-fs Reads counter (rw_stats_enabled=1): n=0 across all gdsio runs, while
gdsio -x0 (GPU direct): 1M seq 2.931, 64K rand 0.694, 4K rand 0.148 GiB/s; -x1 (CPU only): 2.916 / 0.699 / 0.152.
gds_stats attached to a live -x0 4K run: n=87,721 reads, 342 MiB, posix=0, err=0. gds_stats cannot attach in compat mode.
Conclusion: GDS reads reach the GPU without nvidia-fs. Throughput equals the nvidia-fs path on this drive (device-bound).

## Consequence for GDSight
nvfs_io / nvfs_get_p2p_dma_mapping (probe P2) will read 0 for true GDS on this path, so the HDF5 "nvfs_io = 0 means silent compat" test no longer discriminates. Step 4 finds a replacement.

## Revert
Restore 09-cufile.json.before-p2pdma (or 00-cufile.json.pre), delete the two modprobe files, update-initramfs -u, reboot.
