# Is the silent GDS loss library-specific or GDS-internal? (2026-06-25)

Question raised: the `rag_mixed` bypass uses kvikio's 16 KiB threshold — is that a kvikio quirk, or
is there a fallback *internal to GDS* that any user hits? We tested the GDS-internal candidates with
**direct cuFile (no library)**.

## Two distinct mechanisms of silent GDS loss

| mechanism | trigger | who decides | enters cuFile? | gds_stats sees it? |
|---|---|---|---|---|
| **library threshold bypass** | read < 16 KiB | **kvikio** (`KVIKIO_GDS_THRESHOLD`) | **no** (goes POSIX) | **NO** — not a cuFile op |
| **cuFile compat fallback** | file on non-`data=ordered` / unsupported FS | **cuFile (GDS-internal)** | yes, but bounces | yes (aggregate `posix` counter) |
| buffer not registered | — | — | yes, **still true P2P** | (ruled out — not a fallback) |

Direct-cuFile evidence (`workloads/gds_align_probe.c`, 1000×64 KiB):
- `/mnt/nvme1` (ext4 `data=ordered`): cuFileRead 1000 → **nvfs_io 1000, p2p 1000** = true GDS.
- `/` (ext4, no `data=ordered`): cuFileRead 1000 **succeeds** → **nvfs_io 0, p2p 0** = silent POSIX bounce.
- unregistered buffer on the GDS mount: cuFileRead 1000 → **nvfs_io 1000, p2p 1000** = still true P2P
  (cuFile registers internally; **registration is NOT a fallback trigger** — ruled out).

## Honest synthesis (answering "is it representative?")

- The kvikio 16 KiB bypass **is library-specific** — a kvikio policy, not a GDS property. As a
  *representativeness* claim it is narrow (DALI / direct cuFile won't do it).
- But there **is** a genuinely **GDS-internal**, library-independent silent fallback: **cuFile bounces
  to POSIX based on the mount/filesystem** (`data=ordered`/supported-FS), returning success with zero
  GDS. This is the brief's premise, confirmed with no library in the loop.
- **Tool-visibility differs, and this is the useful nuance:** the library bypass is *invisible to
  gds_stats* (reads never enter cuFile); the GDS-internal compat fallback *is* visible to gds_stats in
  aggregate (its `posix` counter), but not per-op/per-file and not with device cost.
- So the defensible, unified contribution is **mechanism-agnostic**: a real pipeline loses GDS via
  *either* path, and **only per-op cross-layer attribution gives one per-op view of "did this op get
  GDS, via which path, and what did it cost at the device"** — `gds_stats` misses the library-bypassed
  ops entirely and shows the compat ones only in aggregate.

## Honest limitations found
- The non-`data=ordered` mount here is `/dev/sda3` (SATA), so the bounced reads don't generate
  `nvme_setup_cmd` (our block probe is nvme-specific) — device-side cost of FS-compat bounce on a
  non-nvme disk is not captured. On an nvme non-`data=ordered` mount it would be.
- The FS-compat fallback is *documented* (the `data=ordered` requirement) — its novelty is per-op/
  per-file detection in mixed-mount setups, not the existence of the requirement.

**Bottom line for the paper:** don't claim the *bypass trigger* is a GDS-internal discovery — it's
not (kvikio policy). Claim the **mechanism-agnostic per-op cross-layer attribution** that unifies
library-bypass + GDS-internal-compat + device-amplification into one view no existing tool provides.
The genuinely GDS-internal fact (mount-based silent compat) is confirmed but largely documented.
