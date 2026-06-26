#!/usr/bin/env bash
# Per-instance DataCrumbs runtime setup. Re-establishes the two pieces of runtime state that a
# cc-snapshot image does NOT preserve, so they must be re-applied on every fresh boot:
#   1. File capabilities on the tracer binary -- stored as the security.capability xattr, which
#      cc-snapshot's tar omits (it uses --selinux --acls but NOT --xattrs). Without them the eBPF
#      server cannot load probes and datacrumbs_run fails silently (set -e) -> stale/empty traces.
#   2. /var/run/datacrumbs -- lives on tmpfs /run, wiped every reboot; datacrumbs_setup does a plain
#      `mkdir -p` of it as a regular user (fails on root-owned /run) and dies under set -e.
# Idempotent; safe to re-run. Run once after each instance launch (alongside mount_gds_nvme.sh).
set -euo pipefail
PREFIX=${DC_PREFIX:-$HOME/dc-prefix}
DCBIN="$PREFIX/sbin/datacrumbs"
[ -x "$DCBIN" ] || { echo "ERROR: $DCBIN not found"; exit 1; }

echo "1) file capabilities on $DCBIN"
sudo setcap cap_sys_admin,cap_bpf,cap_perfmon,cap_dac_read_search+ep "$DCBIN"
echo "   -> $(getcap "$DCBIN")"

echo "2) /var/run/datacrumbs (tmpfs, per-boot)"
sudo mkdir -p /var/run/datacrumbs
sudo chown "$USER:$USER" /var/run/datacrumbs
sudo chmod 775 /var/run/datacrumbs
echo "   -> $(ls -ld /var/run/datacrumbs)"

echo "OK: DataCrumbs runtime ready. (caps + /var/run/datacrumbs)"
