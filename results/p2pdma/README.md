# GDSight on the kernel PCI P2PDMA transport

Run on 2026-11-17 to 2026-11-18, one Chameleon CHI@TACC `compute_liqid` node (`liqid05-grc`): A100-PCIE-40GB,
two Samsung MZ1LB3T8HMLA NVMe drives (GPU and both drives on NUMA node 1, same PCIe switch), Linux
`6.8.0-1051-nvidia` with `CONFIG_PCI_P2PDMA=y`, ext4 `data=ordered` on `nvme1n1`, IOMMU off (`amd_iommu=off`).

The paper measures GDS on the nvidia-fs transport (CUDA 12.6). This directory tests the upstream kernel PCI
P2PDMA transport that GDS supports from CUDA 12.8, and records what GDSight needs to change for it.

## Results

1. **The paper's P2P evidence goes blind on P2PDMA.** With P2PDMA active, the nvidia-fs read counter is 0 for
   every HDF5 configuration, so true GDS and silent compat look the same (`17-...`).
2. **`pci_p2pdma_map_segment` replaces the nvidia-fs marker.** Profiled with ftrace over three reps, it is 0 in
   every host-staged case and nonzero only on true P2P, with counts that match `nvfs_get_p2p_dma_mapping` on the
   same workloads: HDF5 cache off 257, contiguous 72, `gdsio` one per 1 MiB I/O (`18-...`, `19-step4-summary.md`).
3. **The HDF5 finding holds on P2PDMA.** Traced with the new probe, the chunk cache on gives 262 `cuFileRead`
   and 0 P2P mappings; cache off gives 257 and contiguous 72. Every P2PDMA event carries the `corr_id` of a real
   `cuFileRead`, so per-op attribution works unchanged (`22-...`, `24-step5-summary.md`). Rerun with the shipped
   code (separate `p2pdma` plugin, `tools/trace_hdf5_gds_p2pdma.sh`), all 9 runs give the same counts (`28-...`).
4. **Throughput is the same on both transports on this drive** (1 MiB read 2.93 GiB/s), because the drive is the
   limit. These runs do not compare transport performance.

| HDF5, 64 MiB, 256 KiB chunks | cuFileRead | nvidia-fs path: nvfs_io | P2PDMA path: pci_p2pdma_map_segment |
|---|--:|--:|--:|
| chunk cache ON (default) | 262 | 0 | 0 |
| chunk cache OFF | 262 | 256 | 257 |
| contiguous | 8 | 72 | 72 |

## What it took

- **Stack upgrade** (`00`, `01`, `02`): open driver 560.35.05 to 570.211.01, CUDA 12.6 to 12.8.2, libcufile 1.11.1
  to 1.13.1.3. nvidia-fs 2.28.4 stays. The nvidia-fs baseline reproduces on the new stack (`03`, `04`, `07`, `08`).
- **P2PDMA enablement** (`09` to `15`). Setting `properties.use_pci_p2pdma` alone leaves `gdscheck` at
  "NVMe P2PDMA : Unsupported". Also needed, per NVIDIA's GDS troubleshooting guide section 14:
  `block.nvme.use_pci_p2pdma: true` in `cufile.json`; `options nvidia
  NVreg_RegistryDwords="RMForceStaticBar1=1;ForceP2P=0;RmForceDisableIomapWC=1;"` (ForceP2P=0 for pre-Hopper
  GPUs); `options nvme_core multipath=N`; `update-initramfs` and a reboot.
- **GDSight changes.** The paper's scripts and tracer install are unchanged; the P2PDMA path has its own.
  - `external/datacrumbs` on branch `feat/p2pdma` (commit `f900647`): a separate `p2pdma` plugin with the
    `pci_p2pdma_map_segment` kprobe (event type 7), a CUDA 12.8 variant of the cuFile plugin
    (`cufile_cuda128.bpf.c`, `probes_cuda128.json`; the default stays on CUDA 12.6), and host config
    `izzet-gdstrace-node-pcidma` with `p2pdma` in place of `nvidiafs`. Host config names cannot contain digits:
    DataCrumbs strips them.
  - `chameleon/enable_p2pdma.sh [--revert]` applies or undoes the four enablement steps.
  - `chameleon/build_datacrumbs_p2pdma.sh` builds the second install into `~/dc-prefix-p2pdma`.
    `build_datacrumbs.sh` now takes `PREFIX`, `HOSTCFG`, `BUILD` and `SKIP_DFA` from the environment, with the
    paper's values as defaults, and no longer aborts on a broken no-op guard after the install step.
  - `tools/trace_hdf5_gds_p2pdma.sh` is the P2PDMA sibling of `tools/trace_hdf5_gds.sh`. It checks that
    `gdscheck` reports P2PDMA, sets up the tracer runtime, and scores `pci_p2pdma_map_segment`.
  - `23-*.diff` record the first, superseded version (probe inside the nvidiafs plugin), used for `22-...`.

## Not covered

- NIXL, unaligned writes, tail contention and the command cap have not been rerun on P2PDMA.
- IOMMU on. The DMA mapping path may handle P2P segments differently there.
- The first traced set (`22-...`) lost one cache-off run to a known DataCrumbs stop-time issue. The final set
  (`28-...`) has all 9 runs.
- Unexplained: on GDS 1.13 the contiguous read issues 72 P2P mappings and 81 NVMe commands, against 136 and 148 on
  GDS 1.11 (nvidia-fs path both times).

## Node note

During the run, Ubuntu's `unattended-upgrades` installed kernel `6.8.0-149-generic` in the background. Driver
570.211.01 does not build for it (`nv-pci.c: too few arguments to function 'pci_resize_resource'`), which left
dpkg half-configured and made every later `apt-get install` fail. The unbooted kernel was purged, the
`linux-generic` metapackages held, and the apt upgrade timers disabled (`27-...`). On a long lease, disable them
before starting.

## Revert to the paper's configuration

`bash chameleon/enable_p2pdma.sh --revert`, then reboot. By hand: restore `09-cufile.json.before-p2pdma` to
`/etc/cufile.json` (or `00-cufile.json.pre` for the CUDA 12.6 original), delete `/etc/modprobe.d/nvidia-p2pdma.conf`
and `/etc/modprobe.d/nvme.conf`, run `update-initramfs -u`, reboot.

## Files

`00` to `02` stack state before and after the upgrade. `03` to `08` nvidia-fs baseline on CUDA 12.8. `09` to `15`
P2PDMA enablement and proof. `16` to `19` marker search (`fprof.sh` is the ftrace profiler). `20` NVIDIA
`gds-diag` (MagnumIO `d49288c`) on this node. `21` to `24` the first version of the probe and its traced runs
(`run_trace_reps.sh`). `25` and `26` the two tracer installs, `27` the kernel cleanup, `28` the traced runs with
the shipped code (`run_trace_p2pdma_reps.sh`). Each `*-summary.md` is the readable account of its step.
