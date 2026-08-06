# Chameleon true-GDS bring-up — running log

**Goal:** Get a **true NVMe→GPU DMA (GDS) path** working on this Chameleon bare-metal
A100 node, clear the gate (`gdscheck -p` → `NVMe: Supported`, `use_compat_mode: false`),
then run the Appendix-A smoke tests for real (the thing DeltaAI / Delta could not, because
they were compat-only and non-root). This is the live worklog — every issue + fix is recorded
here in order so the run can be followed and reproduced later.

**Started:** 2026-06-07 · **Operator:** Claude (driven by Izzet Yildirim)
**Reference:** `chamREADME.md` (bring-up plan), `gds-trace-brief.md` Appendix A (smoke tests),
NVIDIA [GDS Troubleshooting/Install guide](https://docs.nvidia.com/gpudirect-storage/troubleshooting-guide/index.html#installing-gpudirect-storage).

---

## Node / environment (as found)

| Item | Value |
|---|---|
| Host | Chameleon bare-metal (CHI@TACC `compute_liqid`-class — Muradli's proven node class) |
| OS | Ubuntu 24.04.4 LTS, kernel **6.8.0-111-generic** (≥6.2) |
| CPU | **AMD EPYC 7763** 64-core (256 threads), 250 GiB RAM |
| GPU | 1× **NVIDIA A100-PCIE-40GB** @ `0000:97:00.0`, BAR1 64 GiB, **numa_node 1** |
| Driver / CUDA | **560.35.05** (proprietary, DKMS), CUDA 12.6 |
| Local NVMe | 2× Samsung MZ1LB3T8 **3.5 TB**: `nvme0n1`@`87:00.0`, `nvme1n1`@`88:00.0`, both **numa_node 1**, both ext4 |
| OS root | `/dev/sda3` (480 GB SATA SSD) — NVMe drives are *not* the OS disk |
| Privileges | user `cc`, **passwordless sudo** ✓ (the capability Delta/DeltaAI lacked) |
| GDS userspace (pre-installed) | `libcufile-12-6`, `gds-tools-12-6` **1.11.1.6**; tools at `/usr/local/cuda-12.6/gds/tools/` |

**GPU + both NVMe are all on NUMA node 1** → ideal for P2P DMA.

⚠️ **The NVMe disks contain other users' data** (nvme0n1: RocksDB/YCSB work from Dec 2024;
nvme1n1: a `gds/` dir from Aug 2025, a `jye/` user dir). **Do NOT reformat.** Use a scratch
subdirectory only; leave everything else untouched. (The `chamREADME` said "format nvme0n1",
written before this was known — overridden.)

---

## Decision facts gathered up front

- **Baseline gate = compat-only** (expected): `gdscheck -p` → every backend `Unsupported`,
  `properties.use_compat_mode : true`, because `nvidia_fs` was not installed/loaded.
- **IOMMU is ON in "Translated" mode** (`dmesg: iommu: Default domain type: Translated`,
  AMD-Vi enabled, 172 IOMMU groups). For the traditional nvidia-fs local-NVMe path this must
  be **disabled** (`amd_iommu=off`) → **a reboot will be required** if the gate doesn't pass.
  - This libcufile (1.11.1 / driver 560) predates the IOMMU-friendly **PCI-P2PDMA** path
    (needs CUDA 12.8+/OpenRM 570+; gdscheck here shows no `NVMe P2PDMA` row) — so that
    reboot-free route is not available on this version. Traditional path it is.
- **Prior session today (~01:46)** left a `cufile.log` on nvme0n1 showing it had IOMMU
  *disabled* but still hit `NVMe Driver not registered with nvidia-fs!!!` + compat — i.e. it
  never got nvidia-fs loaded. This instance has since been **re-deployed** (IOMMU back on,
  nvidia-fs not installed), so we start fresh.

---

## Worklog (chronological)

### ✅ Step 1 — Install nvidia-fs (DKMS) — DONE
- `sudo apt-get install -y nvidia-gds-12-6` → pulled `nvidia-fs-dkms 2.28.4`, `nvidia-fs 2.28.4`,
  `nvidia-gds-12-6 12.6.3` (3 new pkgs, **0 removed**, libcufile/gds-tools kept at 1.11.1.6).
- DKMS build reported success: `dkms status` → `nvidia-fs/2.28.4, 6.8.0-111-generic: installed`,
  `nvidia-fs.ko.zst` present, vermagic matches.

### 🛠 Step 2 — Load nvidia_fs — BLOCKED then FIXED (symvers / zstd bug)
**Issue:** `sudo modprobe nvidia_fs` →
`could not insert 'nvidia_fs': Unknown symbol in module`. dmesg:
```
nvidia_fs: Unknown symbol nvidia_p2p_get_pages (err -2)         (+ 7 more nvidia_p2p_* )
```
**Root cause (diagnosed):** The nvidia driver **does** export the `nvidia_p2p_*` symbols
(86 entries in `/proc/kallsyms`, CRCs present in nvidia's DKMS `Module.symvers`). But the
nvidia-fs DKMS build had emitted `MODPOST: "nvidia_p2p_*" undefined!` warnings — it never
linked against nvidia's symbol versions. The harvester `create_nv.symvers.sh` gets the CRCs by
running `nm` on the nvidia `.ko`, and its decompression workaround only handles **`.ko.xz`**,
but Ubuntu 24.04 compresses modules as **`.ko.zst`** → `nm` fails → empty `nv.symvers` →
unresolved `nvidia_p2p_*` at load. (Not an IOMMU problem.)

**Fix 2a (symvers harvest):** Patched `create_nv.symvers.sh` to harvest the `nvidia_p2p_*`
lines **directly from the already-built nvidia DKMS `Module.symvers`**
(`/var/lib/dkms/nvidia/<ver>/<kver>/<arch>/module/Module.symvers`), bypassing the
`nm`-on-`.zst` path. (backup: `create_nv.symvers.sh.orig`)

**Issue 2b (modpost didn't consume it):** After fix 2a the harvest worked but modpost *still*
emitted 8 `undefined` — on kernel 6.8 the Makefile's `cat nv.symvers >> Module.symvers` is dead
(modpost **overwrites** `M=$PWD/Module.symvers` as its output, never reads it as input).
**Fix 2b:** patched the `Makefile` `module:` target to pass `KBUILD_EXTRA_SYMBOLS=$$PWD/nv.symvers`
to the kbuild invocation (the proper modern mechanism). (backup: `Makefile.orig`)
→ rebuild: **0 undefined** ✓. Symbols now resolve at *build* time.

**Issue 2c (THE BIG ONE — proprietary-driver taint):** Even with 0 undefined, `modprobe nvidia_fs`
still fails `err -2` with:
`nvidia_fs: module using GPL-only symbols uses symbols nvidia_p2p_* from proprietary module nvidia`.
**Root cause (hard-confirmed):**
- `modinfo nvidia` → `license: NVIDIA` (**proprietary**); kernel taint = **12289** (bit0 = proprietary module loaded).
- `modinfo nvidia_fs` → `license: GPL v2`, and it uses GPL-only *kernel* symbols.
- Kernel `inherit_taint()` policy: a module that uses GPL-only symbols **may not** import symbols
  from a **proprietary-tainted** module → the `nvidia_p2p_*` imports are refused (`err -2`).
- (`nvidia_p2p_*` are exported plain `__ksymtab_` by nvidia, so it's not a GPL-export problem —
  it's purely the proprietary *taint* of the nvidia module.)
⇒ **nvidia-fs can never load against the proprietary driver on this kernel.**

**Fix 2c (DONE):** Swapped proprietary → **open** nvidia kernel modules (`nvidia-dkms-560-open`,
**same version 560.35.05**; userspace libs untouched). Verified:
- `modinfo nvidia` → `license: Dual MIT/GPL`; per-module taint `OE` (no `P`).
- Rebuilt nvidia-fs → **0 undefined**; **hot-reloaded** the driver stack (no reboot):
  `modprobe -r nvidia_uvm nvidia_drm nvidia_modeset nvidia && modprobe nvidia` →
  `nvidia-smi` OK (A100, 560.35.05) → **`modprobe nvidia_fs` SUCCEEDED.** ✅
- `/proc/driver/nvidia-fs/` now present (`stats`, `version`(2.28), `devcount`, …).

### ✅ Step 2 — nvidia_fs LOADS — DONE (open driver was the unlock)

### 🔬 Step 3/4 — Mount + GATE — partial: ONE blocker left = IOMMU
`gdscheck -p` now shows the open driver recognized but storage still compat:
```
Nvidia Driver Info Status: Supported(Nvidia Open Driver Installed)   <- good
Platform verification succeeded                                       <- good
NVMe : Unsupported   /   properties.use_compat_mode : true            <- still compat
IOMMU: Pass-through or enabled
WARN: GDS is not guaranteed to work ... with iommu=on/pt
Found ACS enabled for switch 86:10.0 / 84:10.0 / 82:00.0 / 80:01.1
cufile.log: cufio-fs:199 NVMe Driver not registered with nvidia-fs!!!
```
**Confirmed IOMMU is the cause, not load order:** unmounted the NVMe, `modprobe -r nvme && modprobe nvme`
(so nvme re-registers with nvidia_fs already loaded) → **still** "NVMe Driver not registered".
nvidia-fs refuses to register the NVMe device for P2P while the IOMMU is in Translated mode.

**Fix (prepared, needs reboot):**
- `/etc/default/grub` — appended `amd_iommu=off` to the **effective** `GRUB_CMDLINE_LINUX_DEFAULT`
  (the file has two such lines; the 2nd/`no_timer_check` one wins). `update-grub` done.
- `/etc/modules-load.d/nvidia-fs.conf` = `nvidia_fs` (autoload at boot).
- `/etc/modprobe.d/nvidia-fs-softdep.conf` = `softdep nvme pre: nvidia_fs` (load nvidia_fs before
  nvme so nvme registers with it). `update-initramfs -u` done.

### ⛔ REBOOT ISSUED 2026-06-07 ~05:18 (to apply amd_iommu=off; ended the agent session)
**On reconnect, THIS is the next action:**
```
# verify IOMMU off + nvidia_fs autoloaded:
grep -o amd_iommu=off /proc/cmdline ; lsmod | grep nvidia_fs
# then run the gate + smoke tests:
bash ~/projects/gdstrace/chameleon/post_reboot_smoke.sh
# (or just start Claude again and say "continue the GDS bring-up")
```
If `amd_iommu=off` is NOT in /proc/cmdline after reboot, the GRUB change didn't take — re-check
`/etc/default/grub` (effective 2nd line) and re-run `sudo update-grub`.
`post_reboot_smoke.sh` will: mount NVMe → (re)register nvme → disable ACS via setpci if still
needed → run `gdscheck -p` GATE → Step 1 ceiling (`-x0` GPUD vs `-x1` CPUONLY vs `-x2`) →
Step 2 aligned-vs-unaligned randread → confirm `gds_stats` can attach. Uses **root
`drop_caches`** before each read (kills the page-cache artifact that inverted earlier Delta runs).

### 🔬 POST-REBOOT (2026-06-07 ~07:12) — IOMMU off confirmed, but a DEEPER blocker found
After reboot: `amd_iommu=off` in `/proc/cmdline` ✓, `iommu_groups` count **0** ✓, gdscheck
`IOMMU: disabled` ✓, open driver + nvidia_fs autoloaded ✓. **But NVMe still `Unsupported`.**
- Disabled ACS on all PCIe bridges via `setpci` (gdscheck no longer flags ACS) — still Unsupported.
- Traced gdscheck: it decides backend support from `/proc/driver/nvidia-fs/modules`, which lists
  only `wekafsio: 1` — **`nvme` is NOT registered** with nvidia-fs.

**ROOT CAUSE (definitive, from nvidia-fs source `nvfs-dma.c`):** nvidia-fs registers the NVMe
backend by `symbol_get`-ing **`nvme_v1_register_nvfs_dma_ops`** (or `nvme_v2_*`) **from the nvme
driver**. It does NOT ftrace/auto-hook a stock nvme. The running Ubuntu `nvme.ko` (v1.0,
`…/kernel/drivers/nvme/host/nvme.ko`) exports **0 nvfs symbols** → registration is silently
skipped → `NVMe: Unsupported`, `use_compat_mode: true`. IOMMU-off + open-driver + ACS-off were all
necessary but **not sufficient**; the missing piece is a **GDS-patched nvme driver**.

⇒ Need an nvme.ko that exports `nvme_v1_register_nvfs_dma_ops`. Candidate paths (researching the
cleanest):
  (a) Ubuntu **`linux-nvidia`** kernel flavour (`linux-image-nvidia-6.8` + `linux-modules-nvidia-fs-nvidia-6.8`)
      — NVIDIA/Canonical kernel with GDS-patched nvme baked in. = new kernel + reboot.
  (b) **MLNX_OFED / DOCA-OFED** — ships a patched nvme (heavier; chamREADME said local NVMe
      "doesn't need OFED" — but that may have assumed the -nvidia kernel).
  (c) Build NVIDIA's patched nvme from source for 6.8.0-111-generic.

**Fix (chosen path = Ubuntu `linux-nvidia` kernel) — VERIFIED:**
- libcufile 1.11.1 has **no** `use_pci_p2pdma` support (would need a full GDS-userspace upgrade to
  12.8/12.9 + likely driver 570 → uncertain) → P2PDMA path rejected for now.
- No DOCA/MLNX apt repo configured (OFED path = add repo + heavy install) → deferred.
- ✅ **`linux-nvidia` 6.8.0-1051 is in the Ubuntu repo and its `nvme.ko` exports
  `nvme_v1_register_nvfs_dma_ops`** (verified by downloading `linux-modules-6.8.0-1051-nvidia` and
  `nm`-ing nvme.ko: 19 nvfs symbols incl. `nvme_nvfs_map_data`). This is the GDS-patched nvme.
  DKMS will rebuild nvidia-open + nvidia-fs for it (symvers fixes already in the source tree).
- ⚠️ Load order for THIS mechanism is the REVERSE of before: nvidia-fs does
  `__symbol_get("nvme_v1_register_nvfs_dma_ops")`, so **nvme must load before nvidia_fs** →
  change softdep to `softdep nvidia_fs pre: nvme`.

### ✅ -nvidia kernel installed & staged (2026-06-07 ~07:25)
Installed (no-recommends, so NO proprietary userspace pulled):
`linux-image-6.8.0-1051-nvidia linux-modules-6.8.0-1051-nvidia linux-headers-6.8.0-1051-nvidia linux-nvidia-headers-6.8.0-1051`.
- **DKMS auto-built `nvidia`(open) + `nvidia-fs` for 6.8.0-1051-nvidia** (nvidia-fs: 0 undefined).
- Installed `-nvidia` `nvme.ko` = **19 nvfs symbols** (patched, exports `nvme_v1_register_nvfs_dma_ops`).
- `amd_iommu=off` confirmed on the -nvidia boot line; **GRUB default = -nvidia** (GRUB_DEFAULT=0,
  newest) + one-time `grub-reboot` set. Old `6.8.0-111-generic` retained as fallback in GRUB menu.
- softdep = `nvidia_fs pre: nvme` (nvme must load first for the symbol_get registration).

### ⛔ REBOOT #2 ISSUED 2026-06-07 ~07:26 — into 6.8.0-1051-nvidia (ends agent session)
**On reconnect:**
```
uname -r                      # expect 6.8.0-1051-nvidia
nm $(modinfo -n nvme | sed 's/.zst//') 2>/dev/null | grep -c nvfs   # patched nvme in use (>0)
bash ~/projects/gdstrace/chameleon/post_reboot_smoke.sh             # gate + smoke
# (or restart Claude and say "continue the GDS bring-up")
```
If the node doesn't come back: it's an official Ubuntu kernel (low risk), but you can pick
"Ubuntu, with Linux 6.8.0-111-generic" from the GRUB menu via Chameleon's serial console to revert.
Expectation this time: gdscheck → `NVMe : Supported`, `use_compat_mode : false`. **Snapshot the instant it does.**

### ✅✅ GATE PASSED — TRUE GDS CONFIRMED (2026-06-07 ~15:31, kernel 6.8.0-1051-nvidia)
After booting the -nvidia kernel: `NVMe : Supported`, `IOMMU: disabled`, open driver, patched nvme
(12 `register_nvfs_dma_ops` in kallsyms). One more fix was needed:

**Issue: per-file fallback to compat despite NVMe Supported.** cufile.log:
`cufio-fs:152 EXT4 journal options not found in mount table for device, can't verify data=ordered
mode journalling` → `cuFileHandleRegister ... using cuFile posix ... compat mode`. cuFile requires
the ext4 mount to **explicitly** list `data=ordered` (it's the default mode but isn't shown under
`rw,relatime`). **Fix:** remount `-o data=ordered` → `findmnt` shows `rw,relatime,data=ordered`.

**Proof of true GDS (decisive):** with `allow_compat_mode:false` (so any fallback ERRORS), a
`gdsio -x0` read **succeeded** (2.95 GiB/s, no error). cuFile per-op stats (`cufile_stats:3`):
`Read: n=2048 posix=0 err=0 MiB=2048` → **all 2048 ops used GDS, zero POSIX fallback.**

Notes:
- `gdscheck` still prints `use_compat_mode : true` = the permissive default in `/etc/cufile.json`
  (`allow_compat_mode:true`). That's wanted for the research (Step 2 needs *silent* fallback to be
  possible/observable). True-GDS is proven by the compat-off test above.
- nvidia-fs `/proc/.../stats` "Ops Read=0" is a red herring — its IO-stats are Disabled; use cuFile
  `cufile_stats` or `gds_stats` instead.
- ACS still enabled after reboot (setpci is runtime-only). Will disable for the perf runs; for a
  durable image this needs a boot-time unit (TODO for snapshot).
- `data=ordered` must be made persistent (fstab or remount) — it resets on reboot. TODO for snapshot.

### ⚠️ INCIDENT (2026-06-07 ~15:35) — disabling ACS broke P2P; nvme1 controller dropped. RECOVERED.
**What I did wrong:** ran `setpci ECAP_ACS+0x6.w=0000` on **all** PCIe bridges (thinking ACS-off
is "best practice" for GDS). On THIS node that destabilized the P2P fabric: the next GDS I/O made
the **nvme1 controller go down** (`CSTS=0x3`), ext4 aborted its journal and remounted read-only.
- ✅ **nvme0n1 (other users' RocksDB data): never affected.**
- ✅ **nvme1n1 (has others' `gds`/`jye` dirs): RECOVERED, no data loss.** PCIe remove+rescan
  (`/sys/bus/pci/.../remove` + `/sys/bus/pci/rescan`) brought the controller back live; `e2fsck -p`
  → exit 0 clean (journal replayed); all dirs intact.
- Re-test confirmed: with ACS-off fabric, even a GDS **read** triggers `controller is down; will
  reset` (kernel auto-recovered). So **ACS-off (as I applied it) is the trigger.**

**Key lessons:**
1. **Do NOT disable ACS on this node.** GDS reads worked perfectly with ACS at firmware-default
   (ON): earlier forced-compat-off read = 2.95 GiB/s, `posix=0`, no controller reset. IOMMU-off is
   sufficient; ACS-off is unnecessary here and harmful.
2. GDS **writes** are also suspect (the first crash was a GDS write) — for smoke tests use `dd` to
   create files and test GDS **reads** only; investigate GDS-write separately/carefully.
3. Both NVMe disks carry other users' data — every GDS op is a (small) risk to a shared disk; reads
   are low-risk (controller drop → protective RO remount, not corruption), writes riskier.

**Fix:** reboot to restore firmware-default ACS (the proven-good state). `setpci` ACS changes are
runtime-only, so a reboot fully reverts them. After reboot: remount `data=ordered`, run GDS-read
smoke tests, **never touch ACS**.

### ⛔ REBOOT #3 ISSUED ~15:40 — to revert the ACS damage (back to ACS firmware-default)
On reconnect: `uname -r`(=6.8.0-1051-nvidia) → remount `-o data=ordered` → smoke READS only.

### ✅ Step 5 — Smoke tests — DONE (clean ACS-default state, kernel 6.8.0-1051-nvidia)
- De-risk: GDS read stable, nvme1 live, 0 errors, no controller-down.
- **Step 1 ceiling** (8G,1M,4thr,cold): `-x0` 2.929 ≈ `-x1` 2.893 ≈ `-x2` 2.896 GiB/s →
  single-PM983 read ceiling (~2.9 GiB/s); drive-bound, not path-bound. (`-x0` verified true GDS.)
- **gds_stats** live-attaches: `Read n=6711 posix=0` (couldn't attach on Delta) ✅
- **Step 2** aligned vs unaligned randread (`posix=0` throughout — no silent fallback from gdsio):
  4K 0.146/0.119 · 64K 0.690/0.635 · 1M 2.945/2.733 GiB/s. Unaligned *slower* (real penalty, NOT
  the Delta page-cache inversion).
- Steps 3/4 (kvikio/DALI ragged-chunk workload) NOT run — readers not installed; now the high-value
  next task (finally meaningful on a real GDS path).

### ✅ Step 6 — Report written: `SMOKE-TEST-REPORT-CHAMELEON.md`; artifacts in `results/chameleon/`.

---
## ✅✅✅ OUTCOME: TRUE GDS WORKING ON CHAMELEON — first of the 3 testbeds to pass the gate.
Gate: `NVMe: Supported`, `IOMMU: disabled`, open driver, `posix=0` under forced-compat-off.

## Persistence TODO before snapshot (gate settings that reset on reboot)
- **`data=ordered` mount is NOT persistent** → next reboot reverts to plain `rw,relatime` and cuFile
  falls back to compat. Fix: add nvme1n1 to `/etc/fstab` `…ext4 rw,data=ordered,nofail 0 2` (or a
  mount unit), OR just re-run `chameleon/post_reboot_smoke.sh` after each boot.
- Leave **ACS at firmware default** (do NOT disable — it broke P2P, see Incident).
- nvidia_fs autoload + `softdep nvidia_fs pre: nvme` already persistent.
- Then `sudo cc-snapshot CC-Ubuntu24.04-CUDA-GDS-$(date +%Y%m%d)` to bank the image.

---
## Dev environment for Step 3 (kvikio/DALI) — set up 2026-06-07 (decision: venv + pip)
Strategy (chosen with user): **C++/CMake via apt (system)**, **Python via one isolated venv on /opt**
(both on the OS root → captured by `cc-snapshot`; venv is cc-owned so pip needs no sudo).
- apt added: `cmake` 3.28.3, `ninja-build` 1.11.1, `pkg-config` (gcc/g++/make/git already present;
  CUDA 12.6 `nvcc` + `libcufile-dev` already present).
- venv `/opt/gds-venv` (Py 3.12, ~1.5 GB): **kvikio-cu12 26.4.0, cupy-cuda12x 14.1.1, numpy 2.4.6,
  nvidia-dali-cuda120 2.1.0**. (System pip is blocked by PEP 668 — venv is the clean path.)
- **kvikio GDS verified end-to-end:** `KVIKIO_COMPAT_MODE=OFF` + `kvikio.CuFile.read` into a cupy
  array → 256 MiB, data correct, `is_compat_mode_preferred=False` (true GDS, not compat).
- Reproducibility (in `chameleon/`): `setup_pyenv.sh` (rebuilds everything), `requirements.txt`
  (top-level), `requirements.lock.txt` (full pin), `verify_kvikio.py` (GDS smoke).
- Activate: `source /opt/gds-venv/bin/activate`.

**Ready for Step 3/4:** drive kvikio (and/or DALI `fn.readers.numpy` GPU) over a ragged/compressed
dataset, compare achieved BW vs the Step-1 ceiling, and use `gds_stats`/`cufile.log` to catch a
*subset* of reads that silently fall back/amplify → the project's motivating figure.

---
## Snapshot created + VALIDATED on a fresh instance — 2026-06-08
**Snapshot:** `grc-ub2404-nvk-gds-a100-cu126-20260607` (project-private Glance image). cc-snapshot
needed `-f` (15.5 GB tripped a size-warning prompt) and `-e /mnt` (exclude NVMe / other tenants'
data; tar also uses `--one-file-system`). virt-sysprep ran (clean image: no SSH keys/machine-id).

**Relaunched from the snapshot (same node class) and re-verified — ALL GREEN:**
- Baked-in stack reproduced perfectly: kernel `6.8.0-1051-nvidia`, `amd_iommu=off`, open driver
  (Dual MIT/GPL), nvidia_fs autoloaded, patched nvme (12 syms), `/opt/gds-venv`, project + scripts.
- Per-instance step (the only one): `chameleon/mount_gds_nvme.sh` → mounts local NVMe `-o data=ordered`.
- Gate: `NVMe: Supported`, `IOMMU: disabled`, "Platform verification succeeded".
- Step 1 ceiling: x0 2.90 ≈ x1 2.93 ≈ x2 2.94 GiB/s (single-PM983 bound; matches original node).
- Step 2: unaligned < aligned (real GDS penalty), `posix=0`. `gds_stats` attaches: `n=87168 posix=0`.
- kvikio: true-GDS read, `is_compat_mode_preferred=False`, data correct.

**3 toolkit bugs found during validation & FIXED (in `chameleon/`):**
1. **`gdscheck | grep -q` under `set -o pipefail`** → grep -q exits on the `NVMe` line (near the top
   of gdscheck's long output), gdscheck gets **SIGPIPE**, pipefail reports failure despite a real
   match → false "NVMe not Supported". Fixed (`mount_gds_nvme.sh`, `post_reboot_smoke.sh`): capture
   output to a var, grep a here-string (no pipe).
2. **`post_reboot_smoke.sh` had a DANGEROUS ACS auto-disable fallback** (triggered by bug #1's
   false-negative) — the exact action that dropped the nvme controller. **Removed** (now just warns;
   IOMMU-off suffices). Also switched Step-1 file creation from a **GDS write → `dd`** (GDS writes
   are the risky op), and corrected the gate logic (NVMe Supported is the criterion; use_compat_mode
   stays true = compat allowed).
3. **`gds_stats` step** didn't enable cuFile stats and used a too-short-lived process → "error
   opening gds stats". Fixed: run the probe gdsio with `cufile_stats:3` + a slow 4K randread that
   stays alive long enough to sample.

**Toolkit relocated to `/opt/gds-tools`** (group-accessible, captured in-image; the project's
`chameleon/` is now a symlink to it). README paths updated to `/opt/gds-tools`.

**v2 snapshot: `grc-ub2404-nvk-gds-a100-cu126-v2`** (date dropped — Glance records `created_at`;
version suffix instead). Bakes the 3 script fixes + the `/opt/gds-tools` relocation; supersedes v1.
Plan: create v2 → confirm `active` → delete v1 (`openstack image delete
301c527d-4e48-404e-9a0c-f95321ea18ea`). v1 had the ACS landmine in `post_reboot_smoke.sh` — do not
run that script from a v1 instance.

**v2 scope (decided):** image = `/opt/gds-tools` (toolkit + README + this LOG + the Chameleon report)
+ `/opt/gds-venv` + system (driver/kernel/nvidia-fs). **`~/projects/gdstrace` is EXCLUDED**
(`cc-snapshot -e /home/cc/projects`) — the research workspace (brief, DeltaAI/Delta reports,
`results/`) stays in home/git, not baked into the shared image. The Chameleon LOG + report are copied
into `/opt/gds-tools/` so the image self-documents.

**DONE (2026-06-07 ~20:42):** v2 `active`, id `f7088c23-a3d5-4d4f-967c-199525250c63`, ~8.2 GB,
visibility `shared`/0-members (grc-project-only). Tagged with properties (kernel/driver/nvidia_fs/
cuda/gds_ready/notes). **v1 deleted.** Single canonical image now =
`grc-ub2404-nvk-gds-a100-cu126-v2`. (Toolkit relocated to `/opt/gds-tools`, project `chameleon/` →
symlink.) Validated end-to-end on a fresh instance earlier; v2 == that validated state + script fixes.

### 🔒 Security rebuild → v3 (2026-06-07 ~21:25)
Caught that v2 inadvertently baked in **secrets**: `~/.claude/.credentials.json` (Claude auth token),
`~/.claude.json` (config/oauth), `~/.claude/projects/` (conversation transcripts), and `~/openrc`
(Chameleon API token) — unacceptable in a grc-shared image. Rebuilt as
**`grc-ub2404-nvk-gds-a100-cu126-v3`** additionally excluding them:
`-e /mnt -e /home/cc/projects -e /home/cc/.claude -e /home/cc/.claude.json -e /home/cc/openrc -e /home/cc/.bash_history`.
Session continuity (memory no longer travels in the image) is handled by **`CLAUDE.md`** in the repo
(auto-loaded by Claude Code after `git clone`). **v2 deleted** once v3 is `active`.
**Workspace now lives in git:** https://github.com/izzet/gdsight.

> ⚠️ **PARKED — security TODO (exclude did NOT work):** on the v3 instance `~/.claude` and `~/projects`
> are STILL present. The `cc-snapshot -e` excludes did not drop them — likely `-e` only appeared to
> work for `/mnt` because tar's `--one-file-system` skips separate filesystems, while the relative
> exclude patterns don't match tar's `./`-prefixed members on the **root** fs (and `~/.claude` is also
> re-created live by the running Claude session). ⇒ **both v2 AND v3 still contain the Claude auth
> token (`~/.claude/.credentials.json`) + `~/openrc` Chameleon token.** Fix later: verify the exclude
> pattern (try `./home/cc/.claude`) or scrub/rotate secrets before snapshot, delete v2, re-snapshot.
> **Parked per user; continuing Step 3 on this instance.**

### ✅ Steps 3 & 4 — silent per-op GDS bypass DEMONSTRATED (2026-06-07 ~22:30)
kvikio over aligned vs **ragged** (compressed-chunk-like) reads: aligned `n=9926` GDS reads, **ragged
`n=0`** (all bypass GDS via kvikio's POSIX path), mixed `n=4956` (only the aligned half) — yet
throughput is identical **~1.23 GiB/s** and `gds_stats` shows **`posix=0`** (blind: the bypass is
*above* cuFile). Can't force GDS on (`KVIKIO_COMPAT_MODE=OFF` unchanged, no error). = the motivating
Figure-1 result: a real vendor reader silently skips GDS for a subset of ops, invisible to throughput
AND to `gds_stats`. Write-up: `results/step3/STEP3-FINDINGS.md`; script `step3/step3_ragged.py`.
Caveat: libcufile **segfaults at process exit** on this stack (kvikio-26 / libcufile-12.6) → counters
read **live** via `gds_stats -p`.

### Step 3 follow-ups #1 / #2 / #3 (2026-06-07 late) — details in `results/step3/STEP3-FINDINGS.md`
- **#1 cost:** on this single PM983 the bypass is invisible to throughput, CPU, **and** page-cache
  (`gdsio -x0 ≈ -x1`; kvikio aligned ≈ ragged CPU) — the strongest "coarse tools blind" form.
- **#2 realism:** real `.npy` data offset = 128 B (unaligned) → kvikio (8 threads) **and** DALI numpy
  GPU reader **both** bypass GDS for 100% of reads (`n=0`). A ubiquitous format silently defeats GDS.
- **#3 tracer:** added `external/dftracer` + `external/brahma` submodules + `tools/gds_trace_preload.c`
  (LD_PRELOAD cuFile+POSIX interposer). **Finding:** `libkvikio` `dlsym`'s cuFile + uses the
  **async/batch** API (`cuFileReadAsync`/`cuFileBatchIOSubmit`), not `cuFileRead`, so libc LD_PRELOAD
  is structurally blind → the tracer must be **GOTCHA over the full cuFile API surface** (+ eBPF for
  reads that never enter cuFile). **Full DFTracer build = next session.**
- Repo `izzet/gdstrace`: **4 commits, UNPUSHED** (needs your GitHub auth — see push note above).

### ⚠️ CORRECTION (2026-06-07) — Step-3/4 "silent POSIX bypass" finding RETRACTED
The "silent bypass" / Figure-1 claim was a **measurement error**: I read the cuFile **per-GPU `n=`**
counter (which is 0 for unaligned reads) instead of `GLOBAL Read: ok` / the **kernel nvidia-fs
`Reads`/`readMiB`** counters. Ground truth (enabled `rw_stats_enabled=1`, `gdsio -x0` control matched
exactly): **ALL kvikio reads — aligned, ragged, `.npy` — do real NVMe→GPU DMA** (Δreads
6000/6019/468, ΔreadMiB 3195/3190/324, matching each workload). **No bypass; GDS works.** Real (modest)
takeaways: cuFile per-GPU userspace stats are misleading; mild unaligned read-splitting (468 vs ~300);
**the project's silent-fallback premise is NOT yet demonstrated here.** Corrected in
`results/step3/STEP3-FINDINGS.md` (original kept, marked SUPERSEDED) + report Step 3&4. (Caught by
Izzet's skepticism.)

---

## Helper scripts written (`chameleon/`)
- `provision_gds.sh` — full reproducible bring-up (install + symvers fixes + open-driver swap +
  GRUB/iommu + autoload). Re-runnable; good basis for the snapshot / other nodes.
- `post_reboot_smoke.sh` — the gate + smoke runner described above.

## State changes made to the node (for reversibility)
| Change | Backup / undo |
|---|---|
| `create_nv.symvers.sh` patched (zstd WA) | `…/nvidia-fs-2.28.4/create_nv.symvers.sh.orig` |
| `Makefile` patched (KBUILD_EXTRA_SYMBOLS) | `…/nvidia-fs-2.28.4/Makefile.orig` |
| proprietary→open nvidia driver | `apt install nvidia-driver-560` to revert |
| `/etc/default/grub` (+amd_iommu=off) | `/etc/default/grub.orig` |
| `/etc/modules-load.d/nvidia-fs.conf`, `/etc/modprobe.d/nvidia-fs-softdep.conf` | delete to undo |
| NVMe mounted at `/mnt/nvme0`,`/mnt/nvme1`; scratch `/mnt/nvme1/gdstrace-smoke` | no format — data preserved |

## Open risks / things to watch (post-reboot)
- After `amd_iommu=off`, expect `NVMe: Supported` + `use_compat_mode: false`. If NVMe still
  Unsupported, the next lever is **ACS** (the script disables it via `setpci` at runtime — no
  second reboot).
- nvidia-fs **2.28.4** vs GDS userspace **1.11.1**: gdscheck may print a version heuristic; judge
  by real `-x0 > -x1` behavior, not the heuristic line.
- **Snapshot immediately** once the gate passes: `sudo cc-snapshot CC-Ubuntu24.04-CUDA-GDS-$(date +%Y%m%d)`.

---

## New lease bring-up — 2026-07-24 (fresh instance, same node name `izzet-gdstrace-node`)
Fresh Chameleon bare-metal A100-PCIE-40GB, driver 560.35.05, kernel `6.8.0-1051-nvidia`, `nvidia_fs`
loaded, PowerEdge R6525. Bring-up sequence and what this instance did NOT carry over from the image:

1. **NVMe mount.** Both `nvme0n1`/`nvme1n1` (3.5T PM983-class) were genuinely **raw** — verified three
   ways before touching them (`wipefs -n` silent, `sfdisk -l` shows no partition table, `blkid -p` finds
   no signature), so no tenant data was at risk. `DEV=/dev/nvme1n1 FORMAT=1 chameleon/mount_gds_nvme.sh`
   → ext4 (4k) mounted `-o data=ordered` at `/mnt/nvme1`. `gdscheck -p`: **NVMe: Supported | IOMMU: disabled**.
2. **Tracer runtime.** `chameleon/setup_datacrumbs_runtime.sh` (eBPF caps + `/var/run/datacrumbs`).
3. **`rw_stats_enabled=1`** re-applied (per-boot; without it every `/proc/driver/nvidia-fs/stats`
   counter reads 0 even for genuine GDS).
4. **TeX Live** was NOT in the image (installed manually on the previous instance and lost): reinstalled
   `latexmk texlive-{latex-base,latex-recommended,latex-extra,fonts-recommended,pictures,science}`
   (+ `poppler-utils` for `pdftotext` page checks). `bash paper/build.sh` then builds clean.

**Fresh-mkfs gotcha (cost a misleading number).** The first `gdsio` read right after `mkfs` measured
**0.77 GiB/s**, not the expected ~2.5. Cause: `ext4lazyinit` was writing ~120 MB/s of inode tables to
the same drive (`iostat -x` shows it; `pgrep -x ext4lazyinit`). lazyinit ran **350 s** on this 3.5T
volume; the same read then gives **2.93 GiB/s**, i.e. the single-PM983 drive-bound ceiling from the
original Step-1 smoke (~2.9). A mid-lazyinit read still measured 2.49, so the contamination is graded,
not all-or-nothing. Do not benchmark a freshly-formatted drive: wait for `pgrep -x ext4lazyinit` to go
quiet, or `mkfs.ext4 -E lazy_itable_init=0,lazy_journal_init=0`.

**The image's tracer was stale (the one real gap).** `~/dc-prefix/sbin/datacrumbs` was the **Jun 25**
build: its cuFile probe set was only the original five (`cuFileRead/Write/ReadAsync/BatchIOSubmit/
HandleRegister`), missing the Jul-11 `libc:pread` (200005) and per-entry `batchentry` (200006) probes
that the paper's per-op POSIX and per-entry batch results depend on. libbpf/bpftool/`~/dfa-venv` WERE
baked in, so only step 4/6 of `build_datacrumbs.sh` had to be redone:
```
cmake -S . -B build -G Ninja ... -DDATACRUMBS_TRACE_ALL_PROCESSES_OPT=ON   # ON is mandatory, see below
ninja -C build datacrumbs_explorer datacrumbs_generator && ninja -C build run_explorer && ninja -C build run_generator
ninja -k 0 -C build            # regenerates the EMBEDDED skeleton (editing only the .bpf.o does nothing)
cmake -P build/cmake_install.cmake                                  # install without ninja (clean-race)
cp build/data/{categories,probes}-cc-<host>.json ~/dc-prefix/etc/datacrumbs/data/   # note the `cc-` prefix
sudo setcap cap_sys_admin,cap_bpf,cap_perfmon,cap_dac_read_search+ep ~/dc-prefix/sbin/datacrumbs
```
Two notes on top of the existing gotcha list: configure **fails** unless
`DATACRUMBS_CONFIGURED_TRACE_DIR` already exists (`mkdir -p /mnt/nvme1/gdstrace-smoke/dc-traces` first),
and the generated maps are named `…-cc-izzet-gdstrace-node.json` (user prefix), not `…-izzet-gdstrace-node.json`.
The bpf.o clean-race did **not** bite this time (149592 B object, new symbols `pread_gds_entry/exit` +
1648-B `cuFileBatchIOSubmit_entry` present) — check before applying the manual `bpftool gen object` relink.

**Verified end-to-end** (`tools/run_gdstrace_demos.sh gdsio`, 4 MiB GPUD read at 2.49 GiB/s, traced and
run before lazyinit finished):
`cuFileRead=64 → nvfs_io=64` (1:1 true GDS) `→ nvfs_get_p2p_dma_mapping=256` (4 P2P maps per 4 MiB read)
`→ nvme_setup_cmd=488`, plus **`pread=135`** confirming the new probe fires. System-wide events in the
same trace confirm trace-all is active. Known cosmetic issue reconfirmed: the demo's
`datacrumbs_run | grep | head` pipeline SIGKILLs the wrapper ("Killed"), but the trace still flushes;
also `latest_trace()`'s `-newer /tmp/.dc_mark` did not match, so locate the trace under
`dc-traces/YY/MM/DD/` directly rather than trusting the helper.
