#!/usr/bin/env bash
# provision_gds.sh — bring a Chameleon A100 bare-metal node (Ubuntu 24.04, kernel 6.8,
# CUDA 12.6 image) to a TRUE-GDS-capable state. Idempotent-ish; safe to re-run.
#
# What it does (and WHY — see CHAMELEON-GDS-BRINGUP-LOG.md for the full story):
#   1. Install nvidia-fs DKMS (nvidia-gds-12-6 metapackage).
#   2. Swap the PROPRIETARY nvidia kernel driver for the OPEN one (same 560.35.05).
#      -> nvidia-fs is GPL v2 + uses GPL-only kernel symbols, so the kernel's
#         inherit_taint() policy FORBIDS it from importing nvidia_p2p_* from a
#         PROPRIETARY-tainted nvidia module. The open modules are Dual MIT/GPL.
#   3. Patch nvidia-fs's symvers harvester for zstd-compressed modules + feed the
#      nvidia_p2p_* CRCs to modpost via KBUILD_EXTRA_SYMBOLS (Ubuntu 24.04 ships
#      .ko.zst; the stock harvester only handles .ko.xz -> empty nv.symvers ->
#      "nvidia_p2p_* undefined" at modpost).
#   4. Set amd_iommu=off in GRUB + autoload nvidia_fs at boot (GDS needs IOMMU off).
#
# After running this: REBOOT, then run ./post_reboot_smoke.sh
set -euo pipefail
CUDA=/usr/local/cuda-12.6
NVFS_SRC=/usr/src/nvidia-fs-2.28.4   # adjust if a different nvidia-fs version installs

say(){ echo; echo "=== $* ==="; }

say "1/4 Install nvidia-fs DKMS (nvidia-gds-12-6)"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-gds-12-6

say "2/4 Patch nvidia-fs symvers harvest for zstd + KBUILD_EXTRA_SYMBOLS"
# (a) harvest nvidia_p2p_* CRCs directly from nvidia's DKMS Module.symvers
if ! grep -q "zstd workaround" "$NVFS_SRC/create_nv.symvers.sh"; then
  sudo cp -n "$NVFS_SRC/create_nv.symvers.sh" "$NVFS_SRC/create_nv.symvers.sh.orig"
  sudo python3 - "$NVFS_SRC/create_nv.symvers.sh" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
anchor='# Create empty symvers file\necho -n "" > $MOD_SYMVERS\n'
block='''
# --- Ubuntu/zstd workaround (Chameleon GDS bring-up) ---
__nv_ver=$(/sbin/modinfo -F version -k "$KVER" nvidia 2>/dev/null)
for __nv_symv in \\
    /var/lib/dkms/nvidia*/${__nv_ver}/${KVER}/$(uname -m)/module/Module.symvers \\
    /var/lib/dkms/nvidia*/*/${KVER}/$(uname -m)/module/Module.symvers ; do
    if [ -s "$__nv_symv" ] && grep -q "nvidia_p2p_" "$__nv_symv"; then
        grep "nvidia_p2p_" "$__nv_symv" > "$MOD_SYMVERS"
        echo "Created (zstd WA): $MOD_SYMVERS from $__nv_symv"; exit 0
    fi
done
# --- end workaround ---
'''
open(p,"w").write(s.replace(anchor,anchor+block,1))
print("create_nv.symvers.sh patched")
PY
fi
# (b) make modpost actually consume nv.symvers on modern kernels
if ! grep -q "KBUILD_EXTRA_SYMBOLS" "$NVFS_SRC/Makefile"; then
  sudo cp -n "$NVFS_SRC/Makefile" "$NVFS_SRC/Makefile.orig"
  sudo sed -i 's#\$(MAKE) -j4 -C \$(KDIR) \$(MAKE_PARAMS) M=\$\$PWD modules#$(MAKE) -j4 -C $(KDIR) $(MAKE_PARAMS) KBUILD_EXTRA_SYMBOLS=$$PWD/nv.symvers M=$$PWD modules#' "$NVFS_SRC/Makefile"
fi

say "3/4 Swap proprietary nvidia driver -> OPEN kernel modules (same version)"
if [ "$(modinfo -F license nvidia 2>/dev/null)" != "Dual MIT/GPL" ]; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-dkms-560-open
fi
# rebuild nvidia-fs against whatever nvidia driver is now installed
sudo dkms remove nvidia-fs/2.28.4 --all >/dev/null 2>&1 || true
sudo dkms install nvidia-fs/2.28.4
UNDEF=$(sudo grep -c undefined /var/lib/dkms/nvidia-fs/2.28.4/$(uname -r)/$(uname -m)/log/make.log || echo "?")
echo "nvidia-fs modpost undefined symbols: $UNDEF  (must be 0)"

say "4/4 GRUB amd_iommu=off + boot-time module load"
if ! grep -q 'amd_iommu=off' /etc/default/grub; then
  sudo cp -n /etc/default/grub /etc/default/grub.orig
  # append to the EFFECTIVE GRUB_CMDLINE_LINUX_DEFAULT line (the one with no_timer_check)
  sudo python3 - <<'PY'
p="/etc/default/grub"; ls=open(p).read().splitlines(); out=[]
for l in ls:
    if l.startswith("GRUB_CMDLINE_LINUX_DEFAULT=") and "no_timer_check" in l and "amd_iommu" not in l:
        l=l[:-1]+' amd_iommu=off"'
    out.append(l)
open(p,"w").write("\n".join(out)+"\n")
PY
  sudo update-grub
fi
echo 'nvidia_fs' | sudo tee /etc/modules-load.d/nvidia-fs.conf >/dev/null
echo 'softdep nvme pre: nvidia_fs' | sudo tee /etc/modprobe.d/nvidia-fs-softdep.conf >/dev/null
sudo update-initramfs -u

echo
echo ">>> DONE. Now: sudo reboot   then run ./post_reboot_smoke.sh <<<"
