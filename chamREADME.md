# Chameleon true-GDS bring-up — ordered next steps

Goal: get a **true NVMe→GPU DMA (GDS) path working** on a Chameleon bare-metal GPU node,
then run Appendix A for real (which WSL2/DeltaAI/Delta could not). Reference recipe:
Muradli 2025 (IIT) — A100-PCIE-40GB, Ubuntu 24.04, CUDA 12.9, nvidia-fs 2.25, ext4 on local NVMe.

## 0. Launch the instance (you have the reservation)
- Node: **`compute_liqid` @ CHI@TACC** (= Muradli's proven node class: A100-PCIE-40GB, 256GB RAM,
  2× 3.84TB local NVMe). `gpu_h100` @ CHI@TACC is the discrete, no-compose-step alternative.
- COMPOSABLE caveat (compute_liqid): node comes with 1 GPU by default. After boot, run `nvidia-smi`
  and confirm an A100 is present. If not, file a helpdesk ticket "Composable Hardware Configuration
  Request" — do NOT provision until the GPU is composed.
- Image: **`CC-Ubuntu24.04-CUDA`** (driver + CUDA preinstalled; matches the recipe).
- Associate a floating IP, then `ssh cc@<ip>`.
- IN PARALLEL: ask **Muradli** for his exact image/setup or a snapshot — he ran this exact node class;
  it can save hours of nvidia-fs/DKMS debugging.
- Storage note: on these nodes `sda` (SAS 480GB) is the OS root; format `nvme0n1` (or `nvme1n1`)
  for /mnt/gds — i.e. set `DEV=/dev/nvme0n1` in step 1.

## 1. Provision GDS
```bash
scp chameleon/*.sh cc@<ip>:~/
ssh cc@<ip>
lsblk; nvme list                      # find the LOCAL data NVMe (NOT the OS root)
DEV=/dev/nvme0n1 bash provision_gds.sh
```

## 2. THE GATE (moment of truth)
`gdscheck -p` must show `NVMe : Supported` and `use_compat_mode : false`.
This is the single check every prior environment failed. If it shows compat:
disable IOMMU in GRUB + reboot (you have root now), confirm `dkms status` built nvidia-fs,
and ensure data lives on the ext4 NVMe mount.

## 3. Snapshot immediately (solves "no image exists")
```bash
sudo cc-snapshot CC-Ubuntu24.04-CUDA-GDS-$(date +%Y%m%d)
```
Bank the win — never rebuild, and the whole group can reuse it.

## 4. Run the real smoke tests
```bash
bash smoke_gds.sh        # Step 1 ceiling (-x0 should BEAT -x1), Step 2 silent pathology
```
Then Steps 3–4: vendor reader (kvikio/DALI) with varied alignment + the DFTracer GOTCHA
cuFile tracer → per-op cross-layer attribution on a REAL GDS node = Figure 1 of the proposal.

## Notes
- Local-NVMe ext4/xfs GDS does **not** need MLNX_OFED (that's only the RDMA/distributed-FS path).
- With root you can finally pin cache state (`drop_caches`) — fixes the page-cache artifact
  that inverted bandwidth in the earlier runs.
