#!/usr/bin/env bash
# mount_gds_nvme.sh — make a local NVMe GDS-ready by mounting it -o data=ordered.
# Run ONCE per instance after launching from the GDS snapshot (the data=ordered
# mount can't be baked into the image — the NVMe is a different physical disk each
# time). cuFile refuses the GDS path on ext4 unless data=ordered is explicit in the
# mount table; ext4's default IS data=ordered but the kernel doesn't print defaults.
#
# SAFE BY DEFAULT: only mounts an EXISTING ext4. Formatting happens ONLY if you pass
# FORMAT=1 (and it asks you to type the device name to confirm). Never auto-formats —
# these nodes can carry other tenants' data.
#
# Usage:
#   ./mount_gds_nvme.sh                       # auto-pick a non-root NVMe, mount data=ordered
#   DEV=/dev/nvme1n1 ./mount_gds_nvme.sh      # choose the disk explicitly
#   DEV=/dev/nvme1n1 FORMAT=1 ./mount_gds_nvme.sh   # raw disk -> mkfs.ext4 then mount (DESTRUCTIVE)
set -euo pipefail
MNT=${MNT:-/mnt/nvme1}
GDS=/usr/local/cuda-12.6/gds/tools

# --- pick a local data NVMe that is NOT the disk backing / ---
root_pk=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null || true)
if [ -z "${DEV:-}" ]; then
  cand=()
  for d in /dev/nvme*n1; do [ -b "$d" ] || continue; [ "$(basename "$d")" = "$root_pk" ] && continue; cand+=("$d"); done
  [ "${#cand[@]}" -eq 0 ] && { echo "ERROR: no local NVMe found (set DEV=/dev/nvmeXn1)"; exit 1; }
  DEV=""; for c in "${cand[@]}"; do [ "$c" = "/dev/nvme1n1" ] && DEV="$c"; done   # prefer nvme1n1
  [ -z "$DEV" ] && DEV="${cand[0]}"
fi
echo "Using DEV=$DEV  MNT=$MNT"

# --- already mounted data=ordered? nothing to do ---
if mountpoint -q "$MNT"; then
  if findmnt -no OPTIONS "$MNT" | grep -q 'data=ordered'; then
    echo "$MNT already mounted data=ordered — GDS-ready."; exit 0
  fi
  echo "remounting $MNT cleanly"; sudo umount "$MNT"
fi

# --- ensure ext4 (format only if explicitly requested) ---
fstype=$(lsblk -no FSTYPE "$DEV" | head -1 || true)
if [ "$fstype" != "ext4" ]; then
  if [ "${FORMAT:-0}" = "1" ]; then
    echo "!! $DEV fstype='${fstype:-none}'. FORMAT=1 will ERASE it."
    read -rp "Type the device path to confirm wipe ($DEV): " c
    [ "$c" = "$DEV" ] || { echo "aborted."; exit 1; }
    sudo mkfs.ext4 -F "$DEV"
  else
    echo "ERROR: $DEV is not ext4 (fstype='${fstype:-none}'). Re-run with FORMAT=1 to create it (DESTRUCTIVE)." >&2
    exit 2
  fi
fi

# --- mount data=ordered + scratch dir ---
sudo mkdir -p "$MNT"
sudo mount -o data=ordered "$DEV" "$MNT"
sudo mkdir -p "$MNT/gdstrace-smoke" && sudo chown "$(id -u):$(id -g)" "$MNT/gdstrace-smoke"
findmnt -o SOURCE,TARGET,FSTYPE,OPTIONS "$MNT"

# --- confirm the GDS gate. NB: capture to a var and grep a here-string; do NOT
#     pipe `gdscheck | grep -q` under `set -o pipefail` — grep -q exits on the
#     first match (the NVMe line is near the top of gdscheck's long output), so
#     gdscheck gets SIGPIPE and pipefail reports failure despite a real match. ---
ok=0
for i in 1 2 3 4 5; do
  out=$("$GDS/gdscheck" -p 2>/dev/null || true)
  if grep -qE 'NVMe[[:space:]]+: Supported' <<<"$out"; then ok=1; break; fi
  sleep 2
done
if [ "$ok" = 1 ]; then
  echo "OK: NVMe Supported + $MNT data=ordered -> GDS-ready. Scratch: $MNT/gdstrace-smoke"
else
  echo "WARN: gdscheck still not 'NVMe: Supported' after retries. Check kernel(-nvidia)/nvidia_fs/amd_iommu=off — see ../docs/CHAMELEON-GDS-BRINGUP-LOG.md" >&2
fi
