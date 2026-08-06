# GDSight — Smoke Test Results (Chameleon bare-metal A100) — **TRUE GDS ACHIEVED**

**Run by:** Claude (driven by Izzet Yildirim) · **Date:** 2026-06-07
**Node:** Chameleon bare-metal (CHI@TACC `compute_liqid`-class), PowerEdge R6525
**Following:** the Appendix A smoke-test sequence · **Companions:** `SMOKE-TEST-REPORT.md` (DeltaAI GH200),
`SMOKE-TEST-REPORT-DELTA-A100.md` (Delta A100) — both were compat-only dead ends.
**Full bring-up worklog (every issue + fix):** `CHAMELEON-GDS-BRINGUP-LOG.md`

---

## TL;DR

> **Headline: this is the FIRST environment to PASS THE GATE — a real NVMe→GPU DMA (GDS) path.**
> `gdscheck -p` → **`NVMe : Supported`**, **`IOMMU State: Disabled`**, **open driver recognized**,
> "Platform verification succeeded". Proven by measurement, not just config: with
> `allow_compat_mode:false` (so any fallback would *error*), `gdsio -x0` reads **succeed**, and both
> `gds_stats` and cuFile per-op stats report **`posix=0`** — every read traversed the GDS DMA path,
> zero POSIX fallback. The blind-spot experiment the brief needs can now actually be run here.
>
> **What it took (6 stacked blockers, all on the stock Chameleon `CC-Ubuntu24.04-CUDA` image):**
> 1. `nvidia-fs` not installed → `apt install nvidia-gds-12-6`.
> 2. nvidia-fs DKMS symvers harvester only handles `.ko.xz`, not Ubuntu's **`.ko.zst`** → patched.
> 3. `cat nv.symvers >> Module.symvers` is dead on kernel 6.8 → patched Makefile to use
>    **`KBUILD_EXTRA_SYMBOLS`**.
> 4. **nvidia-fs (GPL) cannot import `nvidia_p2p_*` from the PROPRIETARY nvidia driver** (kernel taint
>    policy) → swapped to the **open kernel modules** (`nvidia-dkms-560-open`, same 560.35.05).
> 5. AMD **IOMMU** in Translated mode blocks P2P → **`amd_iommu=off`** in GRUB + reboot.
> 6. **Stock Ubuntu `nvme.ko` has no nvfs hooks** (`nvme_v1_register_nvfs_dma_ops`) → installed the
>    Ubuntu **`linux-nvidia` 6.8.0-1051 kernel**, whose nvme is GDS-patched. + ext4 must be mounted
>    **`data=ordered` explicitly**.
>
> **Smoke results (single local PM983 NVMe, ext4, cold cache, 4 threads):**
> - **Step 1 ceiling:** `-x0` GDS = **2.93 GiB/s** ≈ `-x1` CPU 2.89 ≈ `-x2` bounce 2.90 — all three
>   sit at the **single-drive read ceiling (~2.9 GiB/s)**; the *drive*, not the path, is the limit.
> - **`gds_stats` attaches to a live GDS process** (`Read n=6711 posix=0`) — the very tool that
>   *could not attach* in compat mode on Delta/DeltaAI.
> - **Step 2:** unaligned reads are *slower* than aligned (real, modest GDS penalty) — **not** the
>   page-cache inversion seen on Delta — and stay on the GDS path (`posix=0`, no silent fallback);
>   `gdsio` is well-aligned, so triggering *silent* fallback needs a real workload (Step 3).

---

## Environment

| Item | Value |
|---|---|
| Cluster | **Chameleon** bare-metal, CHI@TACC (`compute_liqid`-class; matches Muradli's node class) |
| Node HW | Dell PowerEdge R6525, **AMD EPYC 7763** 64-core (256 threads), 250 GiB RAM |
| GPU | 1× **NVIDIA A100-PCIE-40GB** @ `0000:97:00.0`, BAR1 64 GiB, **NUMA node 1** |
| Local NVMe | 2× Samsung **MZ1LB3T8** (PM983, 3.84 TB) — `nvme0n1`@`87:00.0`, `nvme1n1`@`88:00.0`, both **NUMA 1** |
| OS | Ubuntu 24.04.4 LTS |
| **Kernel** | **`6.8.0-1051-nvidia`** (Ubuntu `linux-nvidia` flavour — ships the **GDS-patched nvme**) |
| GPU driver | **560.35.05 OPEN kernel modules** (`Dual MIT/GPL`; DKMS) — *required* (see blocker #4) |
| CUDA / GDS userspace | CUDA 12.6, GDS release **1.11.1.6**, libcufile **2.12** |
| nvidia-fs | **2.28.4** (DKMS) |
| Privileges | user `cc`, **passwordless sudo** ✓ (the capability Delta/DeltaAI lacked) |
| IOMMU | **Disabled** (`amd_iommu=off`; `iommu_groups`=0) |
| Test FS | local NVMe `/dev/nvme1n1`, **ext4, `data=ordered`**, mounted `/mnt/nvme1`; scratch `/mnt/nvme1/gdstrace-smoke` |

> ⚠️ **Both NVMe disks hold other users' data** (nvme0: RocksDB/YCSB; nvme1: `gds/`, `jye/`). **Nothing
> was reformatted**; tests use a scratch subdir + `dd`-created files. (One incident dropped the nvme1
> controller mid-test — fully recovered, no data loss; see "Incident" below.)

---

## Step 0 — Gate (the check every prior environment failed) ✅ PASS

```
GDS release version: 1.11.1.6 ; nvidia_fs version: 2.28 ; libcufile version: 2.12 ; Platform: x86_64
DRIVER CONFIGURATION:   NVMe : Supported
GPU 0 NVIDIA A100-PCIE-40GB bar:1 (MiB):65536 supports GDS, IOMMU State: Disabled
PLATFORM INFO:  Nvidia Driver Info Status: Supported(Nvidia Open Driver Installed)
                Platform verification succeeded
```

**Decisive proof of *true* GDS (not just config):** running `gdsio -x0` with `allow_compat_mode:false`
(any fallback ⇒ hard error) **succeeded** at 2.95 GiB/s, and cuFile per-op stats reported
`Read: n=2048 posix=0 err=0 MiB=2048` — **all ops on the GDS DMA path, zero POSIX fallback.**

> Note: `gdscheck` still prints `properties.use_compat_mode : true` — that is the *permissive default*
> in `/etc/cufile.json` (`allow_compat_mode:true`), deliberately kept so the project can observe
> *silent* fallback (Step 2/3). It does **not** mean I/O is running in compat; the test above proves it isn't.

---

## Step 1 — Establish the ceiling (gdsio, local NVMe, cold cache) ✅

8 GiB file, 1 MiB I/O, 4 threads, sequential read, `drop_caches` before each (root — finally possible).

| `gdsio -x` | XferType | Throughput | Avg latency |
|---|---|---:|---:|
| `-x0` GPU_DIRECT (**true GDS**) | GPUD | **2.929 GiB/s** | 1333 µs |
| `-x1` CPU_ONLY | CPUONLY | 2.893 GiB/s | 1350 µs |
| `-x2` storage→CPU→GPU bounce | CPU_GPU | 2.896 GiB/s | 1348 µs |

**Interpretation:** GDS (`-x0`) is marginally fastest, but all three converge at **~2.9 GiB/s = the
single PM983's sequential-read ceiling** (≈3.1 GB/s). On one drive the *device* is the bottleneck, so
GDS's raw-bandwidth advantage over the bounce path is small; its real wins (host-CPU/DRAM bypass,
lower jitter) don't surface as BW here. **To see a GDS bandwidth delta, stripe both NVMe (RAID0) so
the fabric, not the drive, is the limit** — recommended next step. (This is a *different* reason for
`-x0 ≈ -x1` than Delta/DeltaAI, where `-x0` was identical to `-x1` because it *was* the host path in
compat mode. Here `-x0` is genuinely GDS, verified `posix=0`.)

---

## `gds_stats` — the brief's primary instrument now WORKS ✅

Live-attached to a running `-x0` process (loop of seq reads):

```
Read: ok=13422 err=0   Read BandWidth: 2.89 GiB/s   Avg Read Latency: 1367 us
GPU 0  Read: bw=2.89 util(%)=399 n=6711 posix=0 unalign=0 dr=0 err=0 MiB=6711   BufRegister: n=4
```

`posix=0` = pure GDS. **Contrast with Delta/DeltaAI, where `gds_stats` could not attach at all** (no
GDS driver in compat mode). The aggregate instrument the brief leans on is available here — and the
per-op cross-layer signal the project proposes to add can now be developed against a real GDS path.

---

## Step 2 — Aligned vs unaligned random reads (the "silent pathology" probe) ✅

4 GiB random reads, 4 threads, `-x0`, cold cache; `-U` = unaligned offsets.

| I/O size | aligned | unaligned (`-U`) | per-op fallback |
|---|---:|---:|---|
| 4 KiB  | 0.146 GiB/s | 0.119 GiB/s | `posix=0` (no fallback) |
| 64 KiB | 0.690 GiB/s | 0.635 GiB/s | `posix=0` |
| 1 MiB  | 2.945 GiB/s | 2.733 GiB/s | `posix=0` |

**Interpretation:** unaligned is **consistently slower** than aligned — a small, *real* GDS-path
penalty — and importantly **not** the page-cache *inversion* (unaligned appearing 10–40× faster) that
made Step 2 a measurement artifact on Delta/DeltaAI. Root `drop_caches` + O_DIRECT GDS reads give
honest device-speed numbers. `gdsio` issues 4K-aligned I/O, so it **stays on the GDS path
(`posix=0`)** even when "unaligned" — i.e. **no *silent* fallback is triggered by the vendor load
generator.** Reproducing a genuine *silent POSIX fallback / amplification* (the project's target
pathology) requires a real workload with non-4K / compressed / ragged chunks → **Step 3**.

---

## Steps 3 & 4 — Realistic workload — ⚠️ CORRECTED: GDS works, no bypass found

Drove **kvikio** + **DALI** over aligned, ragged, and real `.npy` data. An initial result *appeared*
to show a **silent POSIX bypass** (cuFile per-GPU `n=0` for unaligned reads) — **that was a
measurement error and is RETRACTED.** Kernel ground truth (nvidia-fs IO stats `rw_stats_enabled=1`,
with a `gdsio -x0` positive control that matched exactly) shows **all kvikio reads — aligned, ragged,
and `.npy` — perform real NVMe→GPU DMA**:

| workload | kernel Δreads | kernel ΔreadMiB | verdict |
|---|---:|---:|---|
| gdsio -x0 (control) | 2048 | 2048 (2 GiB) | true GDS |
| kvikio aligned / ragged / .npy | 6000 / 6019 / 468 | 3195 / 3190 / 324 | **all true GDS** |

- **No silent fallback was found** on this stack — GDS engages even for unaligned / `.npy` reads.
- The cuFile **per-GPU userspace stats are misleading** (`n=0`, `posix=0` while the kernel DMA'd) — a
  real *tooling* gotcha, not a pathology. Trust the kernel nvidia-fs `Reads`/`readMiB` counters.
- Mild read splitting for unaligned `.npy` (468 kernel reads vs ~300 logical) — possible small I/O
  amplification, to be quantified — **not** a bypass.
- **Implication for the proposal:** the motivating *silent fallback* pathology is **not yet
  reproduced**; finding a genuine one needs a harder trigger (forced compat, sub-page reads, an
  unsupported-mount subset, or the kernel-depth/eBPF layer). Full detail + retraction:
  `results/step3/STEP3-FINDINGS.md`.

---

## Incident & recovery (full transparency)

While chasing extra P2P throughput I ran `setpci ECAP_ACS+0x6.w=0000` on **all** PCIe bridges to
disable ACS. On this node that destabilized the P2P fabric: the next GDS I/O made the **nvme1
controller drop** (`CSTS=0x3`), and ext4 protectively remounted read-only. **Recovered with zero data
loss** — PCIe remove+rescan brought the controller back, `e2fsck -p` returned clean, all other-user
dirs intact; a reboot restored ACS to firmware default. **Lessons:** (1) on this node **IOMMU-off is
sufficient and ACS should be left at default** — disabling it is unnecessary and harmful; (2) GDS
*reads* are low-risk (a controller drop triggers a protective RO remount, not corruption), GDS
*writes* are riskier (the first drop was a GDS write) — for smoke tests, create files with `dd` and
test GDS reads. Full timeline in `CHAMELEON-GDS-BRINGUP-LOG.md`.

---

## Decision gate → **GO-capable node achieved** (gate prerequisite met; full GO needs Step 3)

- The brief's GO test requires a **real workload below a true-GDS ceiling, lost to a silent
  fallback that coarse tools miss.** Every prior node failed at step zero — **no true-GDS path existed.**
- **Chameleon clears that prerequisite:** a verified NVMe→GPU DMA path (`posix=0`), `gds_stats`
  working, honest cold-cache measurements. The blind-spot experiment can finally be *posed* here.
- **Remaining for a GO/NO-GO verdict:** run Step 3 (kvikio/DALI, ragged chunks) and check whether a
  *subset* of reads silently falls back/amplifies while aggregate tools look fine. That is now a
  tractable next session, not a platform blocker.

---

## What this means for the brief

1. **The load-bearing "userspace-only, no root" assumption is false for getting a GDS path.** A true
   NVMe→GPU GDS path on a generic Ubuntu image needed **root** for: the open-driver swap, `amd_iommu=off`,
   and the `linux-nvidia` (patched-nvme) kernel. DeltaAI/Delta failed precisely because they were
   compat-only **and** non-root. Validation belongs on a **root-capable bare-metal node (Chameleon)**,
   not DeltaAI. The *tracer's* userspace core can still run unprivileged **once such a node exists.**
2. **Exact recipe to reproduce (banked in `chameleon/provision_gds.sh` + the LOG):** open driver +
   nvidia-fs DKMS (with the zstd/KBUILD_EXTRA_SYMBOLS patches) + `linux-nvidia` kernel for the patched
   nvme + `amd_iommu=off` + ext4 `data=ordered`. **Leave ACS at default; do NOT disable it.**
3. **`gds_stats` works here** (it didn't on Delta) — the aggregate baseline the project compares
   against is real, and the per-op cross-layer gap can be demonstrated on live GDS.
4. **Single-drive bandwidth hides GDS's BW benefit** — design Step 3/perf runs around RAID0 of the two
   PM983s (or accept that GDS's win here is CPU/latency, measured via `gds_stats`/profilers, not raw BW).

---

## ⚠️ Persistence TODO before relying on a snapshot

The gate passes **now**, but two settings reset on reboot and must be made durable before/with a
`cc-snapshot` so the image comes up GDS-ready:
- **`data=ordered`** mount — add nvme1n1 to `/etc/fstab` with `data=ordered` (and `nofail`), or a mount unit.
- **(nothing needed for ACS)** — leaving it at firmware default is correct.
- nvidia_fs autoload (`/etc/modules-load.d/nvidia-fs.conf`) + `softdep nvidia_fs pre: nvme` are already persistent.
- Then: `sudo cc-snapshot CC-Ubuntu24.04-CUDA-GDS-$(date +%Y%m%d)` to bank the (hard-won) image.

---

## Reproduction / artifacts (under `~/projects/gdstrace/`)
- `CHAMELEON-GDS-BRINGUP-LOG.md` — full chronological worklog (every blocker, root cause, fix).
- `chameleon/provision_gds.sh` — reproducible bring-up (install + symvers patches + open-driver swap + GRUB/iommu).
- `chameleon/post_reboot_smoke.sh` — gate + smoke runner.
- `results/chameleon/` — `gdscheck.txt`, `step1_ceiling.txt`, `step2_unaligned.txt`, `step2_counters.txt`, `gds_stats.txt`.
- GDS tools: `/usr/local/cuda-12.6/gds/tools/{gdscheck,gdsio,gds_stats}`.

---
*Generated 2026-06-07 on a Chameleon bare-metal A100 node. All numbers from live runs; the gate and
`posix=0` proof are reproducible via `chameleon/post_reboot_smoke.sh`. This is the first of the three
testbeds to reach a working GPUDirect Storage path.*
