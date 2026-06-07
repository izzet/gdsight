#!/usr/bin/env bash
# post_reboot_smoke.sh — run AFTER `sudo reboot` (with amd_iommu=off in effect).
# Clears THE GATE and runs Appendix-A smoke tests on the true-GDS path.
# Safe: never formats; uses a scratch subdir on an existing-ext4 local NVMe.
set -uo pipefail
GDS=/usr/local/cuda-12.6/gds/tools
DEV=/dev/nvme1n1            # local data NVMe (ext4) — NOT the OS root (sda)
MNT=/mnt/nvme1
SCRATCH=$MNT/gdstrace-smoke
OUT=/home/cc/projects/gdstrace/results/chameleon
mkdir -p "$OUT"
SIZE=${SIZE:-8G}; IOSZ=${IOSZ:-1M}; THREADS=${THREADS:-4}
say(){ echo; echo "============ $* ============"; }
drop(){ sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; }

say "0. SANITY"
echo "kernel cmdline:"; cat /proc/cmdline
echo "nvidia license: $(modinfo -F license nvidia 2>/dev/null)  (want Dual MIT/GPL)"
lsmod | grep -q nvidia_fs || sudo modprobe nvidia_fs
echo "nvidia_fs loaded: $(lsmod | grep -c nvidia_fs)"
echo "IOMMU in dmesg:"; sudo dmesg | grep -iE 'AMD-Vi|iommu' | grep -iE 'disabled|Default domain|not enabled' | head -3

say "1. MOUNT $DEV -> $MNT (no format)"
mountpoint -q "$MNT" || { sudo mkdir -p "$MNT"; sudo mount -o data=ordered "$DEV" "$MNT"; }
sudo mkdir -p "$SCRATCH"; sudo chown "$(id -u):$(id -g)" "$SCRATCH"
findmnt -no SOURCE,FSTYPE,TARGET "$MNT"

say "2. ENSURE NVMe REGISTERED WITH nvidia-fs"
# capture+grep here-string — do NOT `gdscheck | grep -q` under pipefail: grep -q
# exits on the NVMe line near the top of gdscheck's output, gdscheck gets SIGPIPE,
# and the pipeline falsely reports failure despite a real match.
reg_ok(){ local o; o=$("$GDS/gdscheck" -p 2>/dev/null || true); grep -qE 'NVMe[[:space:]]+: Supported' <<<"$o"; }
if ! reg_ok; then
  echo "NVMe not yet Supported -> reloading nvme with nvidia_fs present"
  sudo umount "$MNT" 2>/dev/null
  sudo modprobe -r nvme && sudo modprobe nvme && sleep 2
  sudo mount -o data=ordered "$DEV" "$MNT"
fi
if ! reg_ok; then
  # Do NOT auto-disable ACS: on this node class clearing ACS destabilized P2P and
  # dropped the NVMe controller (see LOG). IOMMU-off is sufficient — investigate.
  echo "WARN: NVMe still not Supported. Do NOT disable ACS (it broke P2P here)."
  echo "      Check uname -r=*-nvidia, nvidia=Dual MIT/GPL, nvidia_fs loaded, amd_iommu=off, mount data=ordered."
fi

say "3. THE GATE: gdscheck -p"
"$GDS/gdscheck" -p 2>&1 | tee "$OUT/gdscheck.txt" | grep -E 'NVMe |use_compat_mode|IOMMU|Platform|ACS'
if reg_ok; then
   echo; echo ">>> GATE PASSED: NVMe Supported — true GDS path is live."
   echo "    (use_compat_mode:true just means compat is ALLOWED as fallback; true-GDS is"
   echo "     proven by gds_stats/kvikio posix=0 below.)"
else
   echo; echo ">>> GATE NOT PASSED — inspect $OUT/gdscheck.txt before trusting smoke numbers <<<"
fi

export CUFILE_LOGGING_LEVEL=WARN CUFILE_LOGFILE_PATH="$SCRATCH/cufile.log"
F="$SCRATCH/test.dat"

say "4. STEP 1 — create file (dd, NOT a GDS write) + read ceiling x0/x1/x2"
# create with dd: GDS writes have crashed the nvme controller on this node; reads are safe.
sz=$(numfmt --from=iec "$SIZE"); [ -f "$F" ] || dd if=/dev/zero of="$F" bs=1M count=$((sz/1048576)) oflag=direct status=none
{
for x in 0 1 2; do drop; echo "--- seq read -x$x ---"; \
  "$GDS/gdsio" -f "$F" -d 0 -w "$THREADS" -s "$SIZE" -i "$IOSZ" -I 0 -x $x; done
} 2>&1 | tee "$OUT/step1_ceiling.txt"

say "5. STEP 2 — silent pathology: aligned vs unaligned randread (-x0)"
{
for io in 4K 64K 1M; do
  drop; echo "--- aligned   randread $io ---"; "$GDS/gdsio" -f "$F" -d 0 -w "$THREADS" -s "$SIZE" -i $io -I 2 -x 0
  drop; echo "--- UNaligned randread $io ---"; "$GDS/gdsio" -f "$F" -d 0 -w "$THREADS" -s "$SIZE" -i $io -I 2 -x 0 -U
done
} 2>&1 | tee "$OUT/step2_unaligned.txt"

say "6. gds_stats attaches to a live GDS process (impossible in compat mode)"
# gds_stats can only attach if the target enabled cuFile stats (profile.cufile_stats>=1);
# the default /etc/cufile.json has it at 0, so run this gdsio with a stats-enabled config.
STATS_JSON=/tmp/cufile_stats.json
python3 -c "import re;s=open('/etc/cufile.json').read();open('$STATS_JSON','w').write(re.sub(r'(\"cufile_stats\"\s*:\s*)[0-9]',r'\g<1>3',s))"
drop
# slow 4K randread keeps ONE GDS process alive long enough to sample
# (a fast seq read finishes before gds_stats attaches; -s caps at file size).
CUFILE_ENV_PATH_JSON="$STATS_JSON" "$GDS/gdsio" -f "$F" -d 0 -w "$THREADS" -s "$SIZE" -i 4K -I 2 -x 0 >/dev/null 2>&1 &
GPID=$!; sleep 3
"$GDS/gds_stats" -p "$GPID" -l 3 2>&1 | tee "$OUT/gds_stats.txt" | grep -iE 'Read |GPU 0\(|BandWidth|posix' | head -20
wait "$GPID" 2>/dev/null

echo; echo "ALL DONE. Raw outputs in $OUT/  | cufile.log in $SCRATCH/"
echo "If the gate passed: snapshot now ->  sudo cc-snapshot CC-Ubuntu24.04-CUDA-GDS-\$(date +%Y%m%d)"
